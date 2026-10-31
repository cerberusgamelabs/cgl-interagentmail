"""Explicit per-conversation grants for a foreground owner roster."""
from iam_messenger_access import AccessDenied
from iam_messenger_profiles import snapshot


def scopes(query):
    values=query.get('roster')
    if values is None:return None
    if query.get('read') or 'push' in query or not isinstance(values,list) or not 1<=len(values)<=64:
        raise AccessDenied('Invalid roster')
    seen=set()
    for item in values:
        if (not isinstance(item,dict) or set(item)!={'conversation','grant'} or
            not isinstance(item['conversation'],str) or not 1<=len(item['conversation'])<=512 or
            not isinstance(item['grant'],str) or not 1<=len(item['grant'])<=128 or item['conversation'] in seen):
            raise AccessDenied('Invalid roster scope')
        seen.add(item['conversation'])
    return values


def read(desktop,proofs,principal,installation,values,now):
    from iam_messenger_connector import conversation_name
    from iam_messenger_reads import unread
    result=[]
    for value in values:
        grant=proofs.scope(value['grant'],principal,installation,value['conversation'],'events',now)
        with desktop.connect() as db:count=len(unread(db,value['conversation'],grant.history_from))
        result.append(dict(conversation=value['conversation'],display_name=conversation_name(value['conversation']),
            unread=count,profile=snapshot(desktop,value['conversation'])))
    for value in values:proofs.scope(value['grant'],principal,installation,value['conversation'],'events',now)
    return result
