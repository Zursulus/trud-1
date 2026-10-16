// Dependency-free DOM contract checks. Run with node; no browser is launched.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
class Element{
  constructor(tag='div',text=''){this.tagName=tag;this.children=[];this.textContent=text;this.dataset={};this.style={};this.attrs={};this.listeners={};this.className='';this.classList={add:value=>{this.className+=' '+value;}};}
  get childNodes(){return this.children;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  cloneNode(deep){const node=new Element(this.tagName,this.textContent);node.className=this.className;node.id=this.id;if(deep)node.children=this.children.map(child=>child.cloneNode(true));return node;}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(key,callback){this.listeners[key]=callback;}
  querySelector(){return new Element('button');}
  querySelectorAll(){return [];}
  showModal(){this.open=true;}
  close(){this.open=false;}
}
function descendants(node){return [node,...node.children.flatMap(descendants)];}
function text(node){return node.textContent+node.children.map(text).join('');}
const ids={};
for(const id of ['news-dialog','article-meta','article-title','article-summary','article-body','news-grid','documents-list','important','contacts','contacts-content'])ids[id]=new Element();
ids.important.append(new Element('h1','Исходное письмо'));
const pageUrl=new URL('https://example.test/');
const context={document:{querySelector:selector=>ids[selector.slice(1)]||null,
  createElement:tag=>new Element(tag),createTextNode:value=>new Element('#text',value)},
  window:{location:pageUrl,addEventListener:()=>{}},URL,AbortController,setTimeout,clearTimeout,
  console,history:{state:null,pushState(state,title,url){this.state=state;pageUrl.href=new URL(url,pageUrl).href;}},
  fetch:async()=>({ok:true,json:async()=>({news:[],documents:[]})})};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../../app.js'),'utf8'),context);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  vm.runInContext(`renderHomepageBlocks({announcement:{title:'<script>text only</script>',label:'Важно',subtitle:'Анонс',body:'Абзац\\n\\n- Один\\n- Два',button_text:'Читать',button_url:'javascript:alert(1)',image_url:'https://other.test/image.png'},contacts:{email:'board@example.test',office_hours:'По записи\\nСуббота'}})`,context);
  assert.match(text(ids.important),/<script>text only<\/script>/);
  assert.equal(descendants(ids.important).some(node=>node.tagName==='script'),false);
  assert.equal(descendants(ids.important).some(node=>node.tagName==='a'),false);
  assert.equal(descendants(ids.important).some(node=>node.tagName==='img'),false);
  assert.equal(descendants(ids.important).filter(node=>node.tagName==='li').length,2);
  assert.match(text(ids['contacts-content']),/board@example.test/);
  assert.equal(ids.contacts.hidden,false);
  vm.runInContext(`renderHomepageBlocks({announcement:{title:'Новое',button_text:'Открыть',button_url:'/?news=7',image_url:'/admin/public/announcement/1/image/'},contacts:null})`,context);
  assert.equal(descendants(ids.important).find(node=>node.tagName==='a').href,'https://example.test/?news=7');
  assert.equal(descendants(ids.important).find(node=>node.tagName==='img').src,'https://example.test/admin/public/announcement/1/image/');
  vm.runInContext('renderHomepageBlocks({announcement:null,contacts:null})',context);
  assert.equal(text(ids.important),'Исходное письмо');
  assert.equal(ids.contacts.hidden,true);
  vm.runInContext('renderHomepageBlocks({})',context);
  assert.equal(text(ids.important),'Исходное письмо');
  // Private preview reuses cards and full article renderer, and performs no fetch.
  ids['public-preview-data']=new Element('script',JSON.stringify({news:[{id:7,title:'Предпросмотр',category:'Новость',summary:'Анонс',body:'Полный текст\n\n> Цитата',date:'2026-10-16',featured:true}],documents:[]}));
  context.fetch=()=>{throw new Error('Preview must not fetch public content');};
  await vm.runInContext('loadPublicContent()',context);
  assert.match(text(ids['news-grid']),/Предпросмотр/);
  assert.match(text(ids['news-grid']),/Закреплено/);
  vm.runInContext('openArticle(articles[0],null)',context);
  assert.equal(ids['news-dialog'].open,true);
  assert.equal(ids['article-title'].textContent,'Предпросмотр');
  assert.match(text(ids['article-body']),/Полный текст/);
  assert.equal(descendants(ids['article-body']).some(node=>node.tagName==='blockquote'),true);
  console.log('OK: homepage blocks, safe text/URLs, fallback, private news preview and full article');
})().catch(error=>{console.error(error);process.exitCode=1;});
