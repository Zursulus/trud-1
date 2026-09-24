from collections import Counter

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from .board_polls import (
    BoardDiscussionComment,
    BoardMembership,
    BoardPoll,
    BoardProtocol,
    BoardQuestion,
    BoardVote,
    active_board_membership,
)
from .portal_permissions import CAP_VIEW_ACCOUNT, resolved_accesses


def _guard(request):
    if not request.user.is_authenticated:
        return HttpResponseRedirect(f'{reverse("resident_login")}?next={request.path}')
    if request.user.is_staff:
        return None
    if active_board_membership(request.user) is None:
        raise PermissionDenied
    return None


def _portal_context(request, section='board'):
    accesses = [] if request.user.is_staff else resolved_accesses(request.user, CAP_VIEW_ACCOUNT)
    access = accesses[0] if accesses else None
    return {
        'access': access,
        'accesses': accesses,
        'account': access.account if access else None,
        'active_section': section,
        'board_member': active_board_membership(request.user),
    }


def models_q_active(on):
    from django.db.models import Q
    return Q(ends__isnull=True) | Q(ends__gt=on)


def _eligible_users(poll):
    opening_date = timezone.localtime(poll.opens_at).date()
    memberships = BoardMembership.objects.filter(starts__lte=opening_date).filter(
        models_q_active(opening_date), user__is_active=True,
    ).select_related('user').order_by('user__last_name', 'user__first_name', 'user__username')
    return [membership.user for membership in memberships]


def _user_label(user):
    return user.get_full_name().strip() or user.username


def _question_context(question, current_user, eligible_users):
    votes = list(question.votes.select_related('user').order_by('user__last_name', 'user__first_name', 'user__username'))
    by_user = {vote.user_id: vote for vote in votes}
    counts = Counter(vote.choice for vote in votes)
    return {
        'question': question,
        'my_vote': by_user.get(current_user.pk),
        'votes': votes,
        'counts': {
            'for': counts[BoardVote.CHOICE_FOR],
            'against': counts[BoardVote.CHOICE_AGAINST],
            'abstain': counts[BoardVote.CHOICE_ABSTAIN],
        },
        'not_voted': [_user_label(user) for user in eligible_users if user.pk not in by_user],
        'comments': question.discussion_comments.select_related('author').all(),
    }


def _detail_context(request, poll, error=''):
    questions = list(poll.questions.prefetch_related('votes__user', 'discussion_comments__author').order_by('order', 'id'))
    eligible = _eligible_users(poll)
    context = _portal_context(request)
    context.update({
        'poll': poll,
        'is_open': poll.is_open,
        'question_rows': [_question_context(question, request.user, eligible) for question in questions],
        'eligible_count': len(eligible),
        'eligible_names': [_user_label(user) for user in eligible],
        'choices': BoardVote.CHOICES,
        'protocol': BoardProtocol.objects.filter(poll=poll).first(),
        'form_error': error,
    })
    return context


@never_cache
def board_home(request):
    denied = _guard(request)
    if denied:
        return denied
    polls = list(BoardPoll.objects.prefetch_related('questions').order_by('-opens_at', '-id')[:50])
    rows = []
    for poll in polls:
        question_ids = [question.pk for question in poll.questions.all()]
        answered = BoardVote.objects.filter(question_id__in=question_ids, user=request.user).count() if question_ids else 0
        rows.append({
            'poll': poll,
            'is_open': poll.is_open,
            'question_count': len(question_ids),
            'unanswered': max(0, len(question_ids) - answered),
        })
    context = _portal_context(request)
    context['poll_rows'] = rows
    return TemplateResponse(request, 'water/portal/board_home.html', context)


@never_cache
@csrf_protect
def board_poll_detail(request, poll_id):
    denied = _guard(request)
    if denied:
        return denied
    poll = get_object_or_404(BoardPoll, pk=poll_id)

    if request.method == 'POST':
        if active_board_membership(request.user) is None:
            raise PermissionDenied
        action = request.POST.get('action', '')
        try:
            if action == 'vote':
                try:
                    question_id = int(request.POST.get('question_id', ''))
                except (TypeError, ValueError) as error:
                    raise Http404 from error
                choice = request.POST.get('choice', '')
                comment = request.POST.get('comment', '').strip()
                if choice not in dict(BoardVote.CHOICES) or len(comment) > 2000:
                    raise Http404
                with transaction.atomic():
                    locked_poll = BoardPoll.objects.select_for_update().get(pk=poll.pk)
                    if not locked_poll.is_open:
                        raise ValidationError('Опрос уже закрыт или срок ответа истёк.')
                    question = get_object_or_404(BoardQuestion.objects.select_for_update(), pk=question_id, poll=locked_poll)
                    vote = BoardVote.objects.select_for_update().filter(question=question, user=request.user).first()
                    if vote is None:
                        vote = BoardVote(question=question, user=request.user, choice=choice, comment=comment)
                        reason = 'Первичный голос'
                    else:
                        vote.choice = choice
                        vote.comment = comment
                        reason = 'Изменение голоса до закрытия'
                    vote._audit_actor = request.user
                    vote._audit_reason = reason
                    vote.save()
                return HttpResponseRedirect(reverse('board_poll_detail', args=[poll.pk]))

            if action == 'comment':
                try:
                    question_id = int(request.POST.get('question_id', ''))
                except (TypeError, ValueError) as error:
                    raise Http404 from error
                body = request.POST.get('body', '').strip()
                if not body or len(body) > 3000:
                    raise Http404
                with transaction.atomic():
                    locked_poll = BoardPoll.objects.select_for_update().get(pk=poll.pk)
                    if not locked_poll.is_open:
                        raise ValidationError('Обсуждение закрытого опроса завершено.')
                    question = get_object_or_404(BoardQuestion.objects.select_for_update(), pk=question_id, poll=locked_poll)
                    BoardDiscussionComment.objects.create(question=question, author=request.user, body=body)
                return HttpResponseRedirect(reverse('board_poll_detail', args=[poll.pk]))
            raise Http404
        except ValidationError as error:
            poll.refresh_from_db()
            message = '; '.join(error.messages) if error.messages else 'Не удалось сохранить ответ.'
            return TemplateResponse(request, 'water/portal/board_poll.html', _detail_context(request, poll, message), status=400)

    return TemplateResponse(request, 'water/portal/board_poll.html', _detail_context(request, poll))


@never_cache
def board_protocol_download(request, poll_id):
    denied = _guard(request)
    if denied:
        return denied
    protocol = get_object_or_404(BoardProtocol.objects.select_related('poll'), poll_id=poll_id)
    try:
        handle = protocol.document.open('rb')
    except FileNotFoundError as error:
        raise Http404 from error
    return FileResponse(handle, as_attachment=True, filename=protocol.original_name)
