const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../core.js');

test('detects extensionless HLS and signed MP4 while excluding insecure URLs', () => {
  assert.equal(core.mediaType('https://cdn.test/play?token=a', 'application/vnd.apple.mpegurl; charset=utf-8'), 'application/vnd.apple.mpegurl');
  assert.equal(core.mediaType('https://cdn.test/a.M3U8?sig=a'), 'application/vnd.apple.mpegurl');
  assert.equal(core.mediaType('https://cdn.test/video?sig=a', 'video/mp4'), 'video/mp4');
  for(const value of ['segment.m4s','track.cmfv','track.cmfa','init-video.mp4','video_init.mp4']) assert.equal(core.mediaType('https://cdn.test/'+value,'video/mp4'),null);
  for (const value of ['http://cdn.test/a.m3u8', 'blob:https://page.test/a', 'file:///video.mp4', 'https://user:pass@cdn.test/a.mp4']) assert.equal(core.mediaType(value), null);
});

test('only scoped allowlisted headers survive; never forward browser or hop headers', () => {
  assert.deepEqual(core.requestHeaders([
    {name:'Cookie',value:'session=one'}, {name:'Authorization',value:'Bearer token'},
    {name:'Referer',value:'https://page.test/'}, {name:'Host',value:'wrong.test'},
    {name:'Connection',value:'close'}, {name:'X-Custom',value:'private'},
    {name:'Origin',value:'https://page.test/\r\nInjected: yes'}
  ]), {cookie:'session=one',authorization:'Bearer token',referer:'https://page.test/'});
});

test('candidate score follows active player frame rather than unrelated iframe ad', () => {
  const playing = [{frameId:5,playing:true,width:1280,height:720,src:'blob:https://page.test/main'}];
  assert.ok(core.score({frameId:5,url:'https://cdn.test/main.m3u8',mimeType:'application/vnd.apple.mpegurl'},playing) > core.score({frameId:7,url:'https://ads.test/ad.mp4',mimeType:'video/mp4'},playing));
  const direct = [{frameId:5,playing:true,width:1280,height:720,src:'https://cdn.test/main.mp4'}];
  assert.ok(core.score({frameId:5,url:'https://cdn.test/main.mp4',mimeType:'video/mp4'},direct) > core.score({frameId:5,url:'https://cdn.test/old.mp4',mimeType:'video/mp4'},direct));
});

test('refresh identity retains exact origin and pathname while allowing signed query renewal', () => {
  assert.equal(core.identity('https://cdn.test/main.m3u8?token=old'),core.identity('https://cdn.test/main.m3u8?token=new'));
  assert.notEqual(core.identity('https://cdn.test/main.m3u8'),core.identity('https://ads.test/main.m3u8'));
  assert.notEqual(core.identity('https://cdn.test/main.m3u8'),core.identity('https://cdn.test/ad.m3u8'));
  assert.notEqual(core.identity('https://cdn.test/play?channel=main&token=old'),core.identity('https://cdn.test/play?channel=ad&token=new'));
  assert.notEqual(core.identity('https://cdn.test/play?id=main'),core.identity('https://cdn.test/play?id=ad'));
  assert.notEqual(core.identity('https://cdn.test/play?unknown=main'),core.identity('https://cdn.test/play?unknown=ad'));
  assert.equal(core.identity('https://cdn.test/play?channel=main&token=old&Expires=123'),core.identity('https://cdn.test/play?channel=main&token=new&Expires=456'));
  assert.equal(core.identity('https://cdn.test/play?channel=main&X-Amz-Signature=old&X-Amz-Date=123'),core.identity('https://cdn.test/play?channel=main&X-Amz-Signature=new&X-Amz-Date=456'));
});

test('discovers explicitly advertised HLS alternatives inside embedded FLV players', () => {
  const hls='https://media.test/live/match.m3u8?token=signed';
  const frame='https://player.test/e?hls='+encodeURIComponent(hls)+'&flv='+encodeURIComponent('https://media.test/live/match.flv')+'&protocol=hls';
  const result=core.discoverMedia(frame);
  assert.deepEqual(result.candidates,[{url:hls,mimeType:'application/vnd.apple.mpegurl'}]);
  assert.deepEqual(result.formats,['flv']);
  // Never guess an HLS address from a stream that only advertises FLV.
  assert.equal(core.discoverMedia('https://media.test/live/match.flv').candidates.length,0);
});

test('nested source discovery is bounded and excludes insecure sources',()=>{
  const result=core.discoverMedia('https://player.test/?src='+encodeURIComponent('https://embed.test/?file='+encodeURIComponent('https://cdn.test/movie.mp4')));
  assert.equal(result.candidates[0].url,'https://cdn.test/movie.mp4');
  for(const address of ['http://cdn.test/movie.mp4','file:///movie.mp4','https://user:pass@cdn.test/movie.mp4','javascript:alert(1)']) {
    assert.equal(core.discoverMedia('https://player.test/?url='+encodeURIComponent(address)).candidates.length,0);
  }
  assert.deepEqual(core.discoverMedia('https://cdn.test/main.mpd').formats,['dash']);
});
