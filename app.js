const dialog=document.querySelector('#news-dialog');
const articleMeta=document.querySelector('#article-meta');
const articleTitle=document.querySelector('#article-title');
const articleSummary=document.querySelector('#article-summary');
const articleBody=document.querySelector('#article-body');
const newsGrid=document.querySelector('#news-grid');
const documentsList=document.querySelector('#documents-list');
let articles=[];
let contentStatus='loading';
let lastArticleTrigger=null;
let renderedArticleKey='';
let closePending=false;

function formatDate(value){
  const [year,month,day]=String(value||'').split('-');
  return year&&month&&day?`${day}.${month}.${year}`:'';
}

function renderArticleBody(value){
  const blocks=String(value||'').split(/\n\s*\n/).map(v=>v.trim()).filter(Boolean);
  return blocks.map((block,index)=>{
    const lines=block.split('\n').map(v=>v.trim()).filter(Boolean);
    if(lines.length&&lines.every(line=>/^[-•]\s+/.test(line))){
      const ul=document.createElement('ul');
      ul.className='article-list';
      lines.forEach(line=>{const li=document.createElement('li');li.textContent=line.replace(/^[-•]\s+/,'');ul.append(li);});
      return ul;
    }
    if(lines.length===1&&lines[0].startsWith('> ')){
      const quote=document.createElement('blockquote');
      quote.textContent=lines[0].slice(2);
      return quote;
    }
    const p=document.createElement('p');
    p.textContent=lines.join(' ');
    if(index===0)p.className='article-lead';
    if(/^Актуально на|^Нормативная основа:|^Материал информационный/.test(p.textContent))p.classList.add('article-note');
    return p;
  });
}

function articleIdFromUrl(){
  return new URL(window.location.href).searchParams.get('news');
}

function closeDialog(restoreFocus=false){
  const wasOpen=dialog.open;
  if(wasOpen)dialog.close();
  renderedArticleKey='';
  if(!wasOpen&&!restoreFocus)return;
  // History traversal restores its own focus after popstate has finished.
  setTimeout(()=>{
    if(!dialog.open&&!articleIdFromUrl()&&lastArticleTrigger&&lastArticleTrigger.isConnected)lastArticleTrigger.focus({preventScroll:true});
  },0);
}

function syncArticleFromUrl(){
  const wasClosing=closePending;
  closePending=false;
  const id=articleIdFromUrl();
  if(!id){closeDialog(wasClosing);return;}
  const article=articles.find(item=>String(item.id)===id);
  lastArticleTrigger=Array.from(newsGrid.querySelectorAll('[data-news-id]')).find(button=>button.dataset.newsId===id)||lastArticleTrigger;
  const key=`${id}:${contentStatus}`;
  if(key!==renderedArticleKey){
    articleMeta.textContent=article?[String(article.category||'Новость').toUpperCase(),formatDate(article.date)].filter(Boolean).join(' · '):'';
    articleTitle.textContent=article?article.title:contentStatus==='loading'?'Загружаем новость…':'Новость недоступна';
    articleSummary.textContent=article?article.summary||'':'';
    articleSummary.hidden=!articleSummary.textContent;
    const message=contentStatus==='error'?'Не удалось загрузить новость. Закройте окно и повторите загрузку в разделе новостей.':'Возможно, новость снята с публикации или ссылка устарела.';
    articleBody.replaceChildren(...renderArticleBody(article?article.body:contentStatus==='loading'?'Подождите, пожалуйста.':message));
    dialog.setAttribute('aria-busy',String(contentStatus==='loading'));
    dialog.scrollTop=0;
    renderedArticleKey=key;
  }
  if(!dialog.open){
    lastArticleTrigger=Array.from(newsGrid.querySelectorAll('[data-news-id]')).find(button=>button.dataset.newsId===id)||null;
    dialog.showModal();
  }
}

function openArticle(article,trigger){
  if(closePending)return;
  const url=new URL(window.location.href);
  if(url.searchParams.get('news')!==String(article.id)){
    const returnUrl=url.pathname+url.search+url.hash;
    url.searchParams.set('news',article.id);
    history.pushState({...history.state,trudNewsReturn:returnUrl},'',url.pathname+url.search+url.hash);
  }
  lastArticleTrigger=trigger;
  syncArticleFromUrl();
}

function requestArticleClose(){
  if(closePending)return;
  const url=new URL(window.location.href);
  if(!url.searchParams.has('news')){closeDialog();return;}
  closeDialog();
  if(history.state&&typeof history.state.trudNewsReturn==='string'){
    closePending=true;
    history.back();
  }else{
    // A directly opened article has no page entry to return to in this tab.
    url.searchParams.delete('news');
    history.replaceState(history.state,'',url.pathname+url.search+url.hash);
    syncArticleFromUrl();
  }
}

dialog.querySelector('.close').addEventListener('click',requestArticleClose);
dialog.addEventListener('cancel',event=>{event.preventDefault();requestArticleClose();});
dialog.addEventListener('click',event=>{
  if(event.target!==dialog)return;
  const bounds=dialog.getBoundingClientRect();
  if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)requestArticleClose();
});
window.addEventListener('popstate',syncArticleFromUrl);
window.addEventListener('hashchange',syncArticleFromUrl);

function newsCard(item,index){
  const article=document.createElement('article');
  article.className=`news-card${item.featured?' featured':''}`;
  const meta=document.createElement('div');
  meta.className='card-meta';
  const tag=document.createElement('span');
  tag.className='tag';
  tag.textContent=String(item.category||'Новость').toUpperCase();
  const date=document.createElement('span');
  date.textContent=formatDate(item.date);
  meta.append(tag,date);
  const marker=document.createElement('div');
  marker.className='number';
  marker.setAttribute('aria-hidden','true');
  marker.textContent=String(index+1).padStart(2,'0');
  const title=document.createElement('h3');
  title.textContent=item.title;
  const summary=document.createElement('p');
  summary.textContent=item.summary||'';
  const button=document.createElement('button');
  button.className='read';
  button.type='button';
  button.dataset.newsId=String(item.id);
  button.textContent='Подробнее →';
  button.addEventListener('click',()=>openArticle(item,button));
  article.append(meta,marker,title,summary,button);
  return article;
}

function documentRow(item){
  const link=document.createElement('a');
  link.className='document-row';
  link.href=item.url;
  link.target='_blank';
  link.rel='noopener';
  const main=document.createElement('span');
  main.className='document-main';
  const category=document.createElement('span');
  category.className='document-category';
  category.textContent=item.category;
  const title=document.createElement('strong');
  title.textContent=item.title;
  const description=document.createElement('span');
  description.className='document-description';
  description.textContent=item.description||'';
  main.append(category,title,description);
  const meta=document.createElement('span');
  meta.className='document-meta';
  meta.textContent=[formatDate(item.date),'Скачать'].filter(Boolean).join(' · ');
  link.append(main,meta);
  return link;
}

function contentState(message,retry=false){
  const state=document.createElement('div');
  state.className='content-state';
  const text=document.createElement('p');
  text.setAttribute('role','status');
  text.textContent=message;
  state.append(text);
  if(retry){
    const button=document.createElement('button');
    button.type='button';
    button.className='secondary content-retry';
    button.textContent='Повторить загрузку';
    button.addEventListener('click',()=>loadPublicContent());
    state.append(button);
  }
  return state;
}

async function loadPublicContent(){
  contentStatus='loading';
  newsGrid.setAttribute('aria-busy','true');
  documentsList.setAttribute('aria-busy','true');
  newsGrid.replaceChildren(contentState('Загружаем новости…'));
  documentsList.replaceChildren(contentState('Загружаем документы…'));
  syncArticleFromUrl();
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch('/admin/public/content/',{headers:{Accept:'application/json'},credentials:'same-origin',signal:controller.signal});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const payload=await response.json();
    if(!Array.isArray(payload.news)||!Array.isArray(payload.documents))throw new Error('Invalid public content');
    articles=payload.news;
    contentStatus='ready';
    newsGrid.replaceChildren(...(articles.length?articles.map(newsCard):[contentState('Пока нет опубликованных новостей.')]));
    documentsList.replaceChildren(...(payload.documents.length?payload.documents.map(documentRow):[contentState('Пока нет опубликованных документов.')]));
  }catch(error){
    articles=[];
    contentStatus='error';
    newsGrid.replaceChildren(contentState('Не удалось загрузить новости. Попробуйте ещё раз.',true));
    documentsList.replaceChildren(contentState('Не удалось загрузить документы. Попробуйте ещё раз.',true));
    console.warn('Публичный контент временно недоступен.',error);
  }finally{
    clearTimeout(timeout);
    newsGrid.setAttribute('aria-busy','false');
    documentsList.setAttribute('aria-busy','false');
    syncArticleFromUrl();
  }
}

loadPublicContent();
