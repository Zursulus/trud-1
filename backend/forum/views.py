from datetime import timedelta

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .access import can_moderate, can_write, forum_view, require_writer
from .forms import ModerationForm, PostForm, ProfileForm, ReportForm, TopicForm
from .models import Post, Profile, Report, Section, Topic


def visible_topics(user):
    qs = Topic.objects.select_related('section', 'author__forum_profile')
    return qs if can_moderate(user) else qs.filter(hidden=False)


def page(request, qs):
    return Paginator(qs, 30).get_page(request.GET.get('page'))


def topic_url(obj):
    return redirect('forum:topic', topic_id=obj.pk if isinstance(obj, Topic) else obj.topic_id)


def _write_slot(user):
    # A database lock serializes posts across workers; no process-local cache limit.
    get_user_model().objects.select_for_update().get(pk=user.pk)
    since = timezone.now() - timedelta(minutes=1)
    count = Topic.objects.filter(author=user, created_at__gte=since).count()
    count += Post.objects.filter(author=user, created_at__gte=since).count()
    return count < settings.FORUM_MESSAGES_PER_MINUTE


@forum_view
@require_http_methods(['GET'])
def index(request):
    return render(request, 'forum/index.html', {'sections': Section.objects.all()})


@forum_view
@require_http_methods(['GET'])
def section(request, section_id):
    obj = get_object_or_404(Section, pk=section_id)
    return render(request, 'forum/section.html', {
        'section': obj, 'page': page(request, visible_topics(request.user).filter(section=obj)),
        'can_write': can_write(request.user),
    })


@forum_view
@require_http_methods(['GET', 'POST'])
def new_topic(request, section_id):
    require_writer(request.user)
    obj = get_object_or_404(Section, pk=section_id)
    form = TopicForm(request.POST or None)
    status = 200
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if _write_slot(request.user):
                item = form.save(commit=False)
                item.section, item.author = obj, request.user
                item.save()
                return topic_url(item)
            form.add_error(None, 'Слишком часто. Попробуйте через минуту.')
            status = 429
    return render(request, 'forum/form.html', {'form': form, 'title': 'Новая тема', 'section': obj, 'cancel_url': reverse('forum:section', args=[obj.pk])}, status=status)


@forum_view
@require_http_methods(['GET', 'POST'])
def topic(request, topic_id):
    obj = get_object_or_404(visible_topics(request.user), pk=topic_id)
    form = PostForm(request.POST or None)
    status = 200
    if request.method == 'POST':
        require_writer(request.user)
        with transaction.atomic():
            allowed = _write_slot(request.user)
            obj = get_object_or_404(visible_topics(request.user).select_for_update(of=('self',)), pk=topic_id)
            if obj.closed or obj.hidden:
                raise PermissionDenied('Тема закрыта или скрыта.')
            if form.is_valid():
                if allowed:
                    item = form.save(commit=False)
                    item.topic, item.author = obj, request.user
                    item.save()
                    return topic_url(item)
                form.add_error(None, 'Слишком часто. Попробуйте через минуту.')
                status = 429
    posts = obj.posts.select_related('author__forum_profile', 'topic')
    if not can_moderate(request.user):
        posts = posts.filter(hidden=False)
    posts_page = page(request, posts)
    writer = can_write(request.user)
    for post in posts_page:
        post.can_edit = writer and post.editable_by(request.user)
    return render(request, 'forum/topic.html', {
        'topic': obj, 'page': posts_page, 'form': form,
        'can_write': writer,
        'can_edit_topic': writer and obj.editable_by(request.user),
        'moderation_form': ModerationForm(),
    }, status=status)


def _edit(request, *, topic_id=None, post_id=None):
    require_writer(request.user)
    with transaction.atomic():
        if post_id is not None:
            item = get_object_or_404(Post, pk=post_id)
            get_object_or_404(Topic.objects.select_for_update(), pk=item.topic_id)
            item = get_object_or_404(Post.objects.select_for_update().select_related('topic'), pk=post_id)
            form_class = PostForm
        else:
            item = get_object_or_404(Topic.objects.select_for_update(), pk=topic_id)
            form_class = TopicForm
        if not item.editable_by(request.user):
            raise PermissionDenied('Правка доступна автору в течение 15 минут в открытой теме.')
        form = form_class(request.POST or None, instance=item)
        if request.method == 'POST' and form.is_valid():
            item = form.save(commit=False)
            item.edited_at = timezone.now()
            item.save()
            return topic_url(item)
    return render(request, 'forum/form.html', {'form': form, 'title': 'Правка сообщения', 'topic': item.topic if post_id else item, 'cancel_url': reverse('forum:topic', args=[item.topic_id if post_id else item.pk])})


@forum_view
@require_http_methods(['GET', 'POST'])
def edit_topic(request, topic_id):
    return _edit(request, topic_id=topic_id)


@forum_view
@require_http_methods(['GET', 'POST'])
def edit_post(request, post_id):
    return _edit(request, post_id=post_id)


def _report(request, *, topic_id=None, post_id=None):
    require_writer(request.user)
    if post_id is not None:
        post = get_object_or_404(Post, pk=post_id, hidden=False, topic__hidden=False)
        obj = post.topic
    else:
        obj = get_object_or_404(Topic, pk=topic_id, hidden=False)
        post = None
    form = ReportForm(request.POST or None)
    status = 200
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            reports = Report.objects.filter(reporter=request.user)
            if reports.filter(created_at__gte=timezone.now() - timedelta(minutes=1)).count() >= 5:
                form.add_error(None, 'Слишком часто. Попробуйте через минуту.')
                status = 429
            else:
                if not reports.filter(topic=obj, post=post, resolved_at__isnull=True).exists():
                    report = form.save(commit=False)
                    report.topic, report.post, report.reporter = obj, post, request.user
                    report.full_clean()
                    report.save()
                return topic_url(obj)
    return render(request, 'forum/form.html', {'form': form, 'title': 'Пожаловаться', 'topic': obj, 'cancel_url': reverse('forum:topic', args=[obj.pk])}, status=status)


@forum_view
@require_http_methods(['GET', 'POST'])
def report_topic(request, topic_id):
    return _report(request, topic_id=topic_id)


@forum_view
@require_http_methods(['GET', 'POST'])
def report_post(request, post_id):
    return _report(request, post_id=post_id)


def _moderate(request, *, topic_id=None, post_id=None):
    if not can_moderate(request.user):
        raise PermissionDenied
    form = ModerationForm(request.POST)
    if not form.is_valid():
        return render(request, 'forum/form.html', {'form': form, 'title': 'Модерация'}, status=400)
    with transaction.atomic():
        item = get_object_or_404(
            Post.objects.select_for_update() if post_id else Topic.objects.select_for_update(),
            pk=post_id or topic_id,
        )
        action = form.cleaned_data['action']
        if action in ('hide', 'show'):
            item.hidden = action == 'hide'
            if item.hidden:
                item.hidden_reason = form.cleaned_data['reason']
            item._change_reason = ('Скрытие: ' + item.hidden_reason) if item.hidden else 'Восстановление'
        elif isinstance(item, Topic):
            field = {'pin': 'pinned', 'close': 'closed'}[action]
            setattr(item, field, not getattr(item, field))
            item._change_reason = form.cleaned_data['action']
        else:
            raise PermissionDenied
        item.save()
    return topic_url(item)


@forum_view
@require_POST
def moderate_topic(request, topic_id):
    return _moderate(request, topic_id=topic_id)


@forum_view
@require_POST
def moderate_post(request, post_id):
    return _moderate(request, post_id=post_id)


@forum_view
@require_http_methods(['GET', 'POST'])
def profile(request):
    if request.method == 'GET' and request.user.is_authenticated:
        return redirect('personal_profile')
    if not request.user.is_authenticated:
        raise PermissionDenied
    obj = Profile.objects.filter(user=request.user).first() or Profile(user=request.user)
    form = ProfileForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('forum:index')
    return render(request, 'forum/form.html', {'form': form, 'title': 'Мой ник'})


@require_http_methods(['GET', 'POST'])
def moderation_queue(request):
    if not settings.FORUM_ENABLED:
        raise Http404
    if not can_moderate(request.user):
        raise PermissionDenied
    if request.method == 'POST':
        report_id = request.POST.get('report_id', '')
        if not report_id.isdecimal() or len(report_id) > 19:
            raise Http404
        with transaction.atomic():
            report = get_object_or_404(Report.objects.select_for_update(), pk=report_id)
            if report.resolved_at is None:
                report.resolved_at, report.resolved_by = timezone.now(), request.user
                report.save()
        return redirect('forum_moderation')
    from water.staff_workspace import _base_context
    context = _base_context(request, section='forum')
    context['page'] = page(request, Report.objects.filter(resolved_at__isnull=True).select_related('topic', 'post'))
    return render(request, 'forum/moderation.html', context)


workspace_moderation = admin.site.admin_view(moderation_queue)
