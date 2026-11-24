'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {loadDshKey, proxyEnvironment} = require('../scripts/run-with-dsh.cjs');

function fixture(fn) {
  const base = path.resolve(__dirname, '..', '.test-tmp');
  fs.mkdirSync(base, {recursive:true});
  const root = fs.mkdtempSync(path.join(base, 'dsh-'));
  const home = path.join(root, 'home');
  const entry = path.join(root, 'node_modules', 'dsh', 'bin.js');
  for (const p of [home,path.dirname(entry),path.join(root,'logs'),path.join(root,'node_modules','js-yaml')]) fs.mkdirSync(p,{recursive:true});
  fs.writeFileSync(entry,'');
  fs.writeFileSync(path.join(root,'node_modules','js-yaml','index.js'),'exports.load = JSON.parse;');
  fs.writeFileSync(path.join(root,'logs','server.json'),JSON.stringify({dshHome:home,entry}));
  const credential = path.join(home,'.credentials.yaml');
  fs.writeFileSync(credential,JSON.stringify({refs:{DEEPSEEK_API_KEY:'sk-fixture-only-not-real'}}));
  try { fn({root,home,entry,credential}); }
  finally {
    // Verified, generated test directory only; never the user DSH installation.
    const resolved=fs.realpathSync(root);
    assert.equal(path.dirname(resolved),fs.realpathSync(base));
    fs.rmSync(resolved,{recursive:true,force:true});
  }
}
test('reads key in memory without modifying the credential file',()=>fixture(({root,credential})=>{
  const before=fs.readFileSync(credential);
  assert.equal(loadDshKey(root),'sk-fixture-only-not-real');
  assert.deepEqual(fs.readFileSync(credential),before);
}));
test('rejects unsupported saved credential format',()=>fixture(({root,credential})=>{
  fs.writeFileSync(credential,JSON.stringify({refs:{DEEPSEEK_API_KEY:'env:UNSET'}}));
  assert.throws(()=>loadDshKey(root),/no usable/);
}));
test('rejects a credential home outside the explicit DSH root',()=>fixture(({root,entry})=>{
  fs.writeFileSync(path.join(root,'logs','server.json'),JSON.stringify({dshHome:path.dirname(root),entry}));
  assert.throws(()=>loadDshKey(root),/inside/);
}));
test('maps enabled proxy only to child-process environment',()=>{
  const before=process.env.HTTPS_PROXY;
  assert.deepEqual(proxyEnvironment({ProxyEnable:1,ProxyServer:'http://127.0.0.1:29290'}),
    {HTTP_PROXY:'http://127.0.0.1:29290',HTTPS_PROXY:'http://127.0.0.1:29290',NO_PROXY:'localhost,127.0.0.1,::1'});
  assert.equal(process.env.HTTPS_PROXY,before);
});
test('rejects disabled, credential-bearing and non-HTTP proxy settings',()=>{
  for(const s of [{ProxyEnable:0},{ProxyEnable:1,ProxyServer:'http://user:secret@host:80'},
    {ProxyEnable:1,ProxyServer:'file:///tmp/proxy'}]) assert.throws(()=>proxyEnvironment(s));
});
