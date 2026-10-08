var CACHE='maxai-v1';
var ASSETS=['/','/static/style.css','/static/app.js'];
self.addEventListener('install',function(e){
e.waitUntil(caches.open(CACHE).then(function(c){return c.addAll(ASSETS);}));
self.skipWaiting();
});
self.addEventListener('activate',function(e){
e.waitUntil(caches.keys().then(function(keys){
return Promise.all(keys.filter(function(k){return k!==CACHE;}).map(function(k){return caches.delete(k);}));
}));
self.clients.claim();
});
self.addEventListener('fetch',function(e){
var url=e.request.url;
if(url.indexOf('/api/')>-1||url.indexOf('/site/')>-1)return;
e.respondWith(fetch(e.request).catch(function(){return caches.match(e.request);}));
});
