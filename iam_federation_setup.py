"""Owner-invoked same-account enrollment. Never starts/restarts services."""
import argparse
import json
from uuid import uuid4
from urllib.parse import urlparse, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import URLError
import interagentmail as iam
from iam_federation import FederationError, policy


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def relay_operation(config, operation):
    parsed=urlparse(config['server_url'])
    if parsed.scheme!='wss' or parsed.username or parsed.password:
        raise FederationError('Enrollment requires a secure WebConnect origin')
    request=Request('https://'+parsed.netloc+'/v1/node-federation/'+quote(config['node_id'],safe=''),
        data=json.dumps({'operation':operation}).encode(),method='POST',
        headers={'Authorization':'Bearer '+config['node_token'],'Content-Type':'application/json'})
    try:
        with build_opener(NoRedirect()).open(request,timeout=20) as response:
            raw=response.read(65537)
            if len(raw)>65536:raise FederationError('Enrollment response too large')
            return json.loads(raw)
    except (URLError, ValueError, OSError):
        raise FederationError('Relay enrollment unavailable or rejected; no credentials logged') from None


def run(args):
    from iam_webconnect import load_config, save_config
    from iam_orchestrator import registered_projects
    config=load_config()
    if args.operation=='configure':
        info=relay_operation(config,'info')
        if info.get('node')!=config['node_id'] or any(p not in info.get('peers',[]) for p in args.peer):
            raise FederationError('Every peer must be a different computer owned by this account')
        config['federation']=dict(enabled=True,owner_account_id=info['owner_account_id'],epoch=uuid4().hex,
            peers=list(dict.fromkeys(args.peer)),mailboxes=list(dict.fromkeys(args.mailbox or [])))
        policy(config,registered_projects())
        if args.allow_owner_chat:
            if info.get('messenger_enabled') is not True or not info.get('home_server'):
                raise FederationError('Owner Messenger is not enabled on this relay')
            messenger=config.get('messenger',{})
            if ((messenger.get('home_server') not in (None,info['home_server'])) or
                    (messenger.get('owner_account_id') not in (None,info['messenger_owner_account_id']))):
                raise FederationError('Existing Messenger identity differs; explicit review required')
            config['messenger']={**messenger,'enabled':True,'send_enabled':True,
                'home_server':info['home_server'],'owner_account_id':info['messenger_owner_account_id']}
        save_config(config)
        print('Enrollment saved. No services restarted. Reload only the connector and Messenger when safe.')
        print('Pair owner chat through Messenger > Link another computer; approve it on the peer computer.')
    elif args.operation=='disable':
        config['federation']={**config.get('federation',{}),'enabled':False}
        save_config(config)
        relay_operation(config,'unlink')
        print('Local cross-PC routing disabled and relay enrollment revoked. Revoke remote device grants in Messenger too.')
    elif args.operation=='status':
        value=config.get('federation',{})
        print(json.dumps({'enabled':value.get('enabled') is True,'node':config['node_id'],
            'peers':value.get('peers',[]),'mailboxes':value.get('mailboxes',[])},indent=2))


def add_parser(sub):
    command=sub.add_parser('federation',help='Link same-account computers through WebConnect (opt-in).')
    operations=command.add_subparsers(dest='operation',required=True)
    configure=operations.add_parser('configure',help='Enroll selected peers and registered mailboxes; does not restart anything.')
    configure.add_argument('--peer',action='append',required=True)
    configure.add_argument('--mailbox',action='append',default=[])
    configure.add_argument('--allow-owner-chat',action='store_true',help='Enable signed Messenger pairing; still requires local device approval.')
    configure.set_defaults(func=run)
    for name in ('disable','status'):
        operations.add_parser(name).set_defaults(func=run)
