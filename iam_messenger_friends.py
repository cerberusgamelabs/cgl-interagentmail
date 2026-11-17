"""Participant-scoped external conversations; never owner pairing or private history."""
import json
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from iam_service import IAMError


def policy_request(frame, config, store, registered):
    from iam_messenger_consent import ExternalContactReview
    result=dict(protocol='iam-webconnect',version=1,type='messenger_friend_policy_result',id=frame.get('id'))
    try:
        if (set(frame)!={'protocol','version','type','id','installation_id','mailbox','home_server','owner_account_id'}
                or frame['protocol']!='iam-webconnect' or frame['version']!=1
                or frame['type']!='messenger_friend_policy'
                or config.get('enabled') is not True
                or config.get('messenger',{}).get('friendships_enabled') is not True
                or frame['installation_id']!=config.get('node_id')
                or frame['home_server']!=config.get('messenger',{}).get('home_server')
                or frame['owner_account_id']!=config.get('messenger',{}).get('owner_account_id')
                or frame['mailbox'] not in registered):
            raise IAMError('External contact unavailable')
        review=ExternalContactReview(store)
        status=review.status(frame['mailbox'],config)
        result.update(permitted=status['permitted'],revision=status['revision'],scope=review.scope(config)[0])
    except (IAMError,ValueError,TypeError,KeyError):
        result['error']='friend_policy_unavailable'
    return result


def main():
    import argparse
    import os
    from pathlib import Path
    from iam_service import mailbox_service
    parser=argparse.ArgumentParser(description='Intentional external friendship operations; never owner authorization')
    parser.add_argument('operation',choices=['self','roster','invite','discover','request','decide','send','history','read','inbox','attachment','delivery_settings'])
    parser.add_argument('--mailbox')
    parser.add_argument('--value-file',help='UTF-8 JSON operation fields; avoids shell quoting for messages')
    args=parser.parse_args()
    service=mailbox_service(os.environ.get('IAM_PROJECT_ROOT') or Path.cwd(),args.mailbox)
    value={}
    if args.value_file:
        path=Path(args.value_file)
        if path.stat().st_size>65000:raise IAMError('Operation file too large')
        value=json.loads(path.read_text(encoding='utf-8'))
    print(json.dumps(friend_operation(service,args.operation,value),ensure_ascii=False))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise IAMError('Friend endpoint redirect refused')


def friend_operation(service, operation, value, *, internal=False):
    """Bound local caller; configured node credential stays in the HTTPS header."""
    from iam_webconnect import load_config
    from iam_desktop import DesktopStore
    from iam_messenger_consent import ExternalContactReview
    allowed={'self','roster','invite','discover','request','decide','send','history','read','inbox','attachment','delivery_settings'}
    if internal:allowed.add('receipt')
    if operation not in allowed:
        raise IAMError('Unsupported friend operation')
    config=load_config()
    if config.get('enabled') is not True or config.get('messenger',{}).get('friendships_enabled') is not True:
        raise IAMError('Friendships are not activated')
    review=ExternalContactReview(DesktopStore())
    policy=review.status(service.address,config)
    if not policy['permitted']:raise IAMError('Current owner permission is required')
    if operation=='delivery_settings':
        # Local only. Never accept sender-supplied mailbox, peer kind or scope.
        import uuid
        from iam_messenger_friend_delivery import FriendQueue
        if not isinstance(value,dict) or set(value) not in ({'id'},{'id','daily_limit','cooldown_seconds','paused','revision'}):
            raise IAMError('Use id to inspect, or id plus all delivery settings and current revision to update')
        try:
            if not isinstance(value['id'],str) or str(uuid.UUID(value['id']))!=value['id']:raise ValueError()
        except (ValueError,TypeError,AttributeError):raise IAMError('A canonical conversation UUID is required') from None
        roster=friend_operation(service,'roster',{})
        relationship=next((r for r in roster['relationships'] if r['id']==value['id'] and r['state']=='active'),None)
        if not relationship:raise IAMError('An active friendship belonging to your mailbox is required')
        current_config=load_config()
        if review.status(service.address,current_config)!=policy:
            raise IAMError('Permission changed; inspect again')
        scope=review.scope(current_config)[0]
        result=FriendQueue().settings(service.address,value['id'],relationship['peer']['kind'],scope=scope,
            updates={k:v for k,v in value.items() if k!='id'} if len(value)>1 else None)
        return dict(result,scope='local incoming deliveries only',unit='delivery turns per rolling 24 hours',
                    defaults={'human_daily_limit':20,'agent_daily_limit':4,'cooldown_seconds':300},
                    bounds={'daily_limit':[1,1000],'cooldown_seconds':[0,86400]},
                    note='Owner consent, revocation, sleep and uncertainty holds still apply. Settings do not resend seen messages or cancel accepted turns.')
    url=urlsplit(config['server_url'])
    if url.scheme!='wss' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise IAMError('A configured secure relay is required')
    body=json.dumps(dict(mailbox=service.address,operation=operation,policy_revision=policy['revision'],value=value)).encode('utf-8')
    if len(body)>65000:raise IAMError('Friend operation too large')
    request=Request('https://'+url.netloc+'/v1/node-friends/'+quote(config['node_id'],safe=''),data=body,
                    headers={'Authorization':'Bearer '+config['node_token'],'Content-Type':'application/json'},method='POST')
    try:
        with build_opener(NoRedirect()).open(request,timeout=30) as response:
            raw=response.read(150001)
            if len(raw)>150000:raise IAMError('Friend response too large')
            result=json.loads(raw)
    except Exception:
        raise IAMError('Friend request unavailable or outcome uncertain; inspect state before retrying') from None
    current=review.status(service.address,load_config())
    if not current['permitted'] or current['revision']!=policy['revision']:
        raise IAMError('Permission changed; response discarded')
    return result


if __name__=='__main__':
    main()
