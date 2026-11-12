const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const core = require('../core.js');

function harness() {
  const events = {}, calls = [], updates = [];
  let now = Date.now(), nativeResult = () => ({ok:true,connected:true,device:'Test TV'});
  const tab = {id:1,url:'https://page.test/watch',title:'Match',mutedInfo:{muted:false}};
  const event = name => ({addListener(callback) { events[name] = callback; }});
  const browser = {
    runtime: {id:'lantern-tv@lantern.local',onMessage:event('message'),getURL:path=>'moz-extension://test/'+path,getManifest:()=>({version:'0.1.1'}),
      async sendNativeMessage(host,message) { calls.push({host,message}); return nativeResult(message); }},
    webRequest: {onBeforeRequest:event('before'),onSendHeaders:event('request'),onHeadersReceived:event('response'),onCompleted:event('complete'),onErrorOccurred:event('error')},
    tabs: {onRemoved:event('removed'),onUpdated:event('updated'),query:async()=>[{...tab}],get:async()=>({...tab}),sendMessage:async()=>{},
      update:async(id,change)=>{ updates.push(change); if ('muted' in change) tab.mutedInfo={muted:change.muted,reason:'extension',extensionId:browser.runtime.id}; return {...tab}; }}
  };
  const context = vm.createContext({browser,LanternCore:core,URL,Date:class extends Date { static now(){ return now; } },crypto:{randomUUID:()=> 'test-session-1234'},setInterval:()=>{}});
  vm.runInContext(fs.readFileSync(require.resolve('../background.js'),'utf8'),context);
  const popup = {url:'moz-extension://test/popup.html'};
  function media(url,frameId=3,tabId=1,mime='application/vnd.apple.mpegurl',cookie='session=one',type='xmlhttprequest') {
    const details = {requestId:String(Math.random()),tabId,frameId,url,type,requestHeaders:[{name:'Cookie',value:cookie},{name:'Host',value:'forbidden'}]};
    events.request(details);
    events.response({...details,statusCode:200,responseHeaders:[{name:'Content-Type',value:mime}]});
  }
  return {events,calls,updates,popup,media,tab,
    play:(url,frameId=3,extra={})=>events.message({type:'play',candidateKey:`${frameId}:${url}`,...extra},popup),
    advance:ms=>{now+=ms;}, result:fn=>{nativeResult=fn;}, flush:()=>new Promise(setImmediate)};
}

test('popup only can initiate casting; content script cannot invoke native commands',async()=>{
  const h=harness(); h.media('https://cdn.test/main.m3u8');
  await h.events.message({type:'play',candidateKey:'3:https://cdn.test/main.m3u8'},{tab:{id:1},frameId:3,url:'https://page.test/watch'});
  assert.equal(h.calls.length,0);
  const result=await h.play('https://cdn.test/main.m3u8');
  assert.equal(result.ok,true); assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].message.source.sessionId,'test-session-1234');
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].message.source.headersByOrigin)),{'https://cdn.test':{cookie:'session=one'}});
  assert.equal(h.calls[0].host,'app.lantern.tv');
});

test('candidate list ranks playing iframe source and does not expose headers',async()=>{
  const h=harness();
  h.media('https://ads.test/ad.mp4',8,1,'video/mp4','ad-secret');
  h.media('https://cdn.test/match.m3u8');
  await h.events.message({type:'players',players:[{playing:true,width:1280,height:720,src:'blob:https://page.test/main'}]},{tab:{id:1},frameId:3});
  const result=await h.events.message({type:'list'},h.popup);
  assert.equal(result.candidates[0].url,'https://cdn.test/match.m3u8');
  assert.equal(JSON.stringify(result).includes('ad-secret'),false);
  assert.equal(JSON.stringify(result).includes('session=one'),false);
  await h.play('https://cdn.test/match.m3u8');
  assert.equal(JSON.stringify(h.calls.find(c=>c.message.action==='play').message.source).includes('ad-secret'),false);
});

test('handoff carries only the chosen player captions and refreshes late tracks',async()=>{
  const h=harness(); const root='https://cdn.test/main.m3u8';
  h.media(root);
  const english={url:'https://caption.test/text?language=en',label:'English'};
  const hindi={url:'https://caption.test/text?language=hi',label:'Hindi'};
  const playFrame=async (frameId, players)=>h.events.message({type:'players',players},{tab:{id:1},frameId});
  const player={playing:true,width:1280,height:720,src:'blob:https://page.test/main'};
  await playFrame(8,[{...player,subtitles:[{url:'https://ads.test/ads.vtt'}]}]);
  await playFrame(3,[{...player,width:320,height:180,subtitles:[{url:'https://cdn.test/preview.vtt'}]},
    {...player,subtitles:[english,{url:'http://caption.test/no.vtt'},english]}]);
  await h.play(root);
  const tracks=call=>JSON.parse(JSON.stringify(call.message.source.subtitles));
  assert.deepEqual(tracks(h.calls[0]),[english]);
  h.advance(11000);
  await playFrame(3,[{...player,subtitles:[english,hindi]}]);
  await h.flush();
  assert.equal(h.calls[1].message.action,'refresh');
  assert.deepEqual(tracks(h.calls[1]),[english,hindi]);
});

test('M4S and MP4 HLS fragments cannot evict or outrank the main playlist',async()=>{
  const h=harness(); h.media('https://cdn.test/main.m3u8');
  await h.events.message({type:'players',players:[{playing:true,width:1280,height:720,src:'blob:https://page.test/main'}]},{tab:{id:1},frameId:3});
  for(let i=0;i<60;i++) {
    h.media(`https://cdn.test/segment-${i}.m4s`,3,1,'video/mp4');
    h.media(`https://cdn.test/segment-${i}.mp4`,3,1,'video/mp4');
  }
  const result=await h.events.message({type:'list'},h.popup);
  assert.equal(result.candidates.length,1);
  assert.equal(result.candidates[0].url,'https://cdn.test/main.m3u8');
  h.media('https://cdn.test/movie.mp4',3,1,'video/mp4','session=one','media');
  const direct=await h.events.message({type:'list'},h.popup);
  assert.equal(direct.candidates.length,2);
  assert.equal(direct.candidates[0].url,'https://cdn.test/main.m3u8');
});

test('stop is tied to the current session and navigation clears all captured state',async()=>{
  const h=harness(); h.media('https://cdn.test/main.m3u8'); await h.play('https://cdn.test/main.m3u8');
  await h.events.message({type:'stop'},h.popup);
  assert.equal(h.calls[1].message.action,'stop'); assert.equal(h.calls[1].message.source.sessionId,'test-session-1234');
  h.events.before({tabId:1,frameId:0,type:'main_frame',url:'https://page.test/another'});
  assert.equal((await h.events.message({type:'list'},h.popup)).candidates.length,0);
  assert.equal((await h.play('https://cdn.test/main.m3u8')).ok,false);
});

test('refresh never changes content query or frame and deduplicates unchanged contexts',async()=>{
  const h=harness(); const root='https://cdn.test/play.m3u8?channel=main&token=old';
  h.media(root,3,1); await h.play(root); h.advance(11000);
  h.media(root,3,1); await h.flush();
  assert.equal(h.calls.filter(c=>c.message.action==='refresh').length,0);
  h.media('https://cdn.test/play.m3u8?channel=ad&token=new',8,1,'application/vnd.apple.mpegurl','ad-frame'); await h.flush();
  assert.equal(h.calls.filter(c=>c.message.action==='refresh').length,0);
  const renewed='https://cdn.test/play.m3u8?channel=main&token=new';
  h.media(renewed,8,1,'application/vnd.apple.mpegurl','wrong-frame'); await h.flush();
  assert.equal(h.calls.filter(c=>c.message.action==='refresh').length,0);
  h.media(renewed,3,1,'application/vnd.apple.mpegurl','main-renewed'); await h.flush();
  const refreshed=h.calls.filter(c=>c.message.action==='refresh');
  assert.equal(refreshed.length,1); assert.equal(refreshed[0].message.source.url,renewed);
  assert.equal(refreshed[0].message.source.rootHeaders.cookie,'main-renewed');
  assert.equal(JSON.stringify(refreshed[0].message.source).includes('wrong-frame'),false);
  h.advance(11000); h.media(renewed,3,1,'application/vnd.apple.mpegurl','main-renewed'); await h.flush();
  assert.equal(h.calls.filter(c=>c.message.action==='refresh').length,1);
});

test('root request headers survive later same-origin segment cookies',async()=>{
  const h=harness(); h.media('https://cdn.test/main.m3u8',3,1,'application/vnd.apple.mpegurl','manifest-cookie');
  h.media('https://cdn.test/chunk.ts',3,1,'video/mp2t','segment-cookie');
  await h.play('https://cdn.test/main.m3u8');
  const source=h.calls[0].message.source;
  assert.equal(source.rootHeaders.cookie,'manifest-cookie');
  assert.equal(source.headersByOrigin['https://cdn.test'].cookie,'segment-cookie');
});

test('refresh retries use bounded backoff and stop after three failures',async()=>{
  const h=harness(); h.media('https://cdn.test/main.m3u8'); await h.play('https://cdn.test/main.m3u8');
  h.result(message=>message.action==='refresh'?{ok:false,error:'Temporary refresh failure'}:{ok:true,connected:true});
  h.advance(11000); h.media('https://cdn.test/main.m3u8?token=new'); await h.flush();
  assert.equal(h.calls.filter(c=>c.message.action==='refresh').length,1);
  h.advance(5000); h.media('https://cdn.test/chunk.ts',3,1,'video/mp2t'); await h.flush();
  assert.equal(h.calls.filter(c=>c.message.action==='refresh').length,1);
  h.advance(6000); h.media('https://cdn.test/chunk.ts',3,1,'video/mp2t'); await h.flush();
  assert.equal(h.calls.filter(c=>c.message.action==='refresh').length,2);
  h.advance(31000); h.media('https://cdn.test/chunk.ts',3,1,'video/mp2t'); await h.flush();
  assert.equal(h.calls.filter(c=>c.message.action==='refresh').length,3);
  h.advance(100000); h.media('https://cdn.test/chunk.ts',3,1,'video/mp2t'); await h.flush();
  assert.equal(h.calls.filter(c=>c.message.action==='refresh').length,3);
});

test('stop restores extension-owned mute but preserves user mute',async()=>{
  const h=harness(); h.media('https://cdn.test/main.m3u8');
  await h.play('https://cdn.test/main.m3u8',3,{muteLocal:true});
  assert.equal(h.tab.mutedInfo.muted,true);
  await h.events.message({type:'stop'},h.popup);
  assert.equal(h.tab.mutedInfo.muted,false);
  await h.play('https://cdn.test/main.m3u8',3,{muteLocal:true});
  h.tab.mutedInfo={muted:true,reason:'user'};
  await h.events.message({type:'stop'},h.popup);
  assert.equal(h.tab.mutedInfo.muted,true);
});

test('failed recast clears stale active session instead of restoring stopped source',async()=>{
  const h=harness(); h.media('https://cdn.test/main.m3u8'); await h.play('https://cdn.test/main.m3u8');
  h.result(message=>message.action==='play'?{ok:false,error:'Cannot load'}:{ok:true});
  await h.play('https://cdn.test/main.m3u8');
  const stopped=await h.events.message({type:'stop'},h.popup);
  assert.equal(stopped.ok,false); assert.equal(h.calls.filter(c=>c.message.action==='stop').length,0);
});

test('already playing DOM source is a candidate and cannot overwrite captured credentials',async()=>{
  const h=harness(), url='https://cdn.test/main.m3u8';
  const sender={tab:{id:1},frameId:3};
  const metadata={type:'players',players:[{playing:true,width:1280,height:720,src:'blob:https://player.test/live'}],candidates:[{url,kind:'iframe',headers:{cookie:'spoofed'}}],formats:['flv']};
  await h.events.message(metadata,sender);
  let result=await h.events.message({type:'list'},h.popup);
  assert.equal(result.candidates[0].url,url);
  assert.equal(result.diagnostics.playingPlayers,1);
  assert.equal(result.diagnostics.version,'0.1.1');
  assert.deepEqual(Array.from(result.diagnostics.observedFormats),['flv']);
  h.media(url,3,1,'application/vnd.apple.mpegurl','captured-request');
  await h.events.message(metadata,sender);
  await h.play(url);
  const source=h.calls.find(c=>c.message.action==='play').message.source;
  assert.equal(source.rootHeaders.cookie,'captured-request');
  assert.equal(JSON.stringify(source).includes('spoofed'),false);
});

test('cached response without request event still discovers an HLS source',async()=>{
  const h=harness(),url='https://cdn.test/cache.m3u8';
  h.events.response({requestId:'cached',tabId:1,frameId:3,url,statusCode:200,type:'xmlhttprequest',responseHeaders:[{name:'Content-Type',value:'application/vnd.apple.mpegurl'}]});
  const result=await h.events.message({type:'list'},h.popup);
  assert.equal(result.candidates[0].url,url);
  await h.play(url);
  assert.deepEqual(Object.keys(h.calls.find(c=>c.message.action==='play').message.source.rootHeaders),[]);
});

test('history and hash changes retain discovery while iframe navigation clears its state',async()=>{
  const h=harness();h.media('https://cdn.test/main.m3u8',3);h.media('https://ads.test/ad.mp4',8,1,'video/mp4');
  h.events.updated(1,{url:'https://page.test/watch#playing'});
  assert.equal((await h.events.message({type:'list'},h.popup)).candidates.length,2);
  h.events.before({tabId:1,frameId:3,type:'sub_frame',url:'https://player.test/new'});
  const result=await h.events.message({type:'list'},h.popup);
  assert.equal(result.candidates.length,1);
  assert.equal(result.candidates[0].url,'https://ads.test/ad.mp4');
});
