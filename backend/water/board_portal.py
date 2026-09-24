from django.urls import reverse

from . import portal_ui
from .board_polls import active_board_membership, pending_board_poll_count


def _augment(request, response):
    context = getattr(response, 'context_data', None)
    if context is None or not request.user.is_authenticated:
        return response
    membership = active_board_membership(request.user)
    pending = pending_board_poll_count(request.user) if membership else 0
    context['board_member'] = membership
    context['board_pending'] = pending
    if pending and 'attention' in context:
        board_item = {
            'kind': 'board',
            'title': 'Правление: ждёт ваш голос',
            'text': f'Открытых опросов без полного ответа: {pending}.',
            'url': reverse('board_poll_home'),
        }
        context['attention'] = [board_item, *context.get('attention', [])][:3]
    return response


def dashboard(request):
    return _augment(request, portal_ui.dashboard(request))


def account_home(request, account_id):
    return _augment(request, portal_ui.account_home(request, account_id))


def more(request, account_id):
    return _augment(request, portal_ui.more(request, account_id))
