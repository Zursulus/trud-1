import json
import re
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_safe


COMMIT_RE = re.compile(r'^[0-9a-f]{40}$')


@require_safe
def deployment_status(request):
    """Expose the deployed revision without revealing server configuration."""
    try:
        with settings.DEPLOYMENT_STATUS_FILE.open(encoding='utf-8') as status_file:
            payload = json.load(status_file)
        commit = payload['commit']
        deployed_at = payload['deployed_at']
        if payload.get('project') != 'trud-1' or not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
            raise ValueError('Invalid deployment status')
        if not isinstance(deployed_at, str):
            raise ValueError('Invalid deployment timestamp')
        datetime.fromisoformat(deployed_at.replace('Z', '+00:00'))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        response = JsonResponse({'status': 'unavailable'}, status=503)
    else:
        response = JsonResponse({
            'project': 'trud-1',
            'commit': commit,
            'deployed_at': deployed_at,
        })
    response['Cache-Control'] = 'no-store'
    return response
