import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(){this.textContent='';this.open=false;this.children=[];this.dataset={};this.classList={add(){}};}
  get childNodes(){return this.children;}
  cloneNode(){return new Element();}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(){}
  addEventListener(){}
  querySelector(){return new Element();}
  querySelectorAll(){return [];}
  showModal(){this.open=true;}
  close(){this.open=false;}
}
const feed={news:Array.from({length:12},(_,i)=>({id:13-i,title:`Recent ${13-i}`})),documents:[]};
const archive={id:1,title:'Archived published article',body:'Archive body'};
for(const initialId of [1,13,999]){
  const elements=new Map();
  const requests=[];
  const context={
    document:{
      querySelector(selector){if(selector==='#public-preview-data')return null;if(!elements.has(selector))elements.set(selector,new Element());return elements.get(selector);},
      createElement(){return new Element();},
    },
    window:{location:{href:`https://synthetic.invalid/?news=${initialId}`},addEventListener(){}},
    history:{state:{}},URL,AbortController,setTimeout,clearTimeout,console,
    fetch:async(url)=>{
      requests.push(url);
      const id=new URL(url,'https://synthetic.invalid').searchParams.get('news');
      return {ok:true,json:async()=>id?{news:id==='1'?[archive]:[],documents:[]}:feed};
    },
  };
  await vm.runInNewContext(fs.readFileSync(new URL('../../app.js',import.meta.url),'utf8'),context);
  await new Promise(setImmediate);
  assert.equal(elements.get('#news-dialog').open,true);
  assert.equal(elements.get('#article-title').textContent,initialId===1?archive.title:initialId===13?'Recent 13':'Новость недоступна');
  assert.equal(elements.get('#news-grid').children.length,12);
  context.window.location.href='https://synthetic.invalid/?news=1';
  context.syncArticleFromUrl();
  await new Promise(setImmediate);
  assert.equal(elements.get('#article-title').textContent,archive.title);
  assert.ok(requests.some(url=>url.includes('news=1')));
  const count=requests.length;
  context.syncArticleFromUrl();
  await new Promise(setImmediate);
  assert.equal(requests.length,count,'Repeated synchronization must use the cached article');
}
console.log('Archived news deep links and history navigation: OK');
