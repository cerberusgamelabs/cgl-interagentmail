import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {MessengerTransport} from '../transport.mjs';
import {mergeEvents} from '../conversation.mjs';

test('newest and older cursors are included in exact signed history content',async()=>{
  const queries=[];
  const transport=new MessengerTransport('https://example.test',{fetchImpl:async(url,options)=>{
    const body=JSON.parse(options.body);
    if(url.endsWith('/challenge')){
      queries.push(JSON.parse(Buffer.from(body.content,'base64').toString()));
      const proof={protocol:'iam-owner-proof',version:1,nonce:'n'.repeat(43),grant_id:body.grant_id,
        home_server:'home',account_id:'owner',device_id:'phone',installation_id:'node',conversation_id:'buddy',operation:'history',
        content_sha256:createHash('sha256').update(Buffer.from(body.content,'base64')).digest('hex'),expires_at:Math.floor(Date.now()/1000)+50};
      return {ok:true,json:async()=>({nonce:proof.nonce,payload:Buffer.from(JSON.stringify(proof)).toString('base64'),expires_at:proof.expires_at})};
    }
    return {ok:true,json:async()=>({events:[],after:[100,'oldest'],more:false})};
  }});
  transport.session={token:'t'.repeat(43),expiresAt:Date.now()+60000,homeServer:'home',accountId:'owner',deviceId:'phone'};
  const sign=async()=> 'AAAA';
  await transport.historyPage('node','buddy','g'.repeat(43),sign,{before:null,limit:50});
  await transport.historyPage('node','buddy','g'.repeat(43),sign,{before:[100,'oldest'],limit:50});
  await transport.historyPage('node','buddy','g'.repeat(43),sign,{after:[200,'newest'],limit:50});
  assert.deepEqual(queries,[{before:null,limit:50},{before:[100,'oldest'],limit:50},{after:[200,'newest'],limit:50}]);
});

test('older-page merge retains older rows when the bounded window fills',()=>{
  const previous=Array.from({length:1000},(_,i)=>({id:String(i+50),created:i+50}));
  const earlier=Array.from({length:50},(_,i)=>({id:String(i),created:i}));
  const merged=mergeEvents(previous,earlier,1000,true);
  assert.equal(merged.length,1000);assert.equal(merged[0].created,0);assert.equal(merged.at(-1).created,999);
});
