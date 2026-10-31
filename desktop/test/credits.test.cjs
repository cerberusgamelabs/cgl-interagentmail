const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../public/app.js'),'utf8');
const context=vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function creditLabel('),source.indexOf('async function refresh()')),context);
test('credit footer rounds to hundredths and distinguishes unavailable from zero',()=>{
  for(const [value,expected] of [[{balance:'62497.5534200000'},'Credits: 62497.55'],[{balance:'1.239'},'Credits: 1.24'],[{balance:0},'Credits: 0.00'],[{unlimited:true},'Credits: unlimited'],[null,'Credits: unavailable'],[{balance:''},'Credits: unavailable'],[{balance:true},'Credits: unavailable'],[{balance:'NaN'},'Credits: unavailable']])assert.equal(context.creditLabel(value),expected);
});
