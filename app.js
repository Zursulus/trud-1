const dialog=document.querySelector('#news-dialog');
const articleMeta=document.querySelector('#article-meta');
const articleTitle=document.querySelector('#article-title');
const articleSummary=document.querySelector('#article-summary');
const articleBody=document.querySelector('#article-body');
const newsGrid=document.querySelector('#news-grid');
const documentsList=document.querySelector('#documents-list');
const importantBlock=document.querySelector('#important');
const originalImportant=importantBlock?Array.from(importantBlock.childNodes,node=>node.cloneNode(true)):[];
const contactsBlock=document.querySelector('#contacts');
const contactsContent=document.querySelector('#contacts-content');
const originalContacts=contactsContent?Array.from(contactsContent.childNodes,node=>node.cloneNode(true)):[];
let articles=[];
const articleLoads=new Map();
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

async function loadSelectedArticle(id){
  articleLoads.set(id,'loading');
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch(`/admin/public/content/?news=${encodeURIComponent(id)}`,{headers:{Accept:'application/json'},credentials:'same-origin',signal:controller.signal});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const payload=await response.json();
    if(!Array.isArray(payload.news))throw new Error('Invalid news content');
    const article=payload.news.find(item=>String(item.id)===id);
    if(article&&!articles.some(item=>String(item.id)===id))articles.push(article);
    articleLoads.set(id,'ready');
  }catch(error){
    articleLoads.set(id,'error');
  }finally{
    clearTimeout(timeout);
    syncArticleFromUrl();
  }
}

function syncArticleFromUrl(){
  const wasClosing=closePending;
  closePending=false;
  const id=articleIdFromUrl();
  if(!id){closeDialog(wasClosing);return;}
  const article=articles.find(item=>String(item.id)===id);
  if(!article&&contentStatus==='ready'&&!articleLoads.has(id))loadSelectedArticle(id);
  const status=article?contentStatus:(articleLoads.get(id)||contentStatus);
  lastArticleTrigger=Array.from(newsGrid.querySelectorAll('[data-news-id]')).find(button=>button.dataset.newsId===id)||lastArticleTrigger;
  const key=`${id}:${status}`;
  if(key!==renderedArticleKey){
    articleMeta.textContent=article?[String(article.category||'Новость').toUpperCase(),formatDate(article.date)].filter(Boolean).join(' · '):'';
    articleTitle.textContent=article?article.title:status==='loading'?'Загружаем новость…':'Новость недоступна';
    articleSummary.textContent=article?article.summary||'':'';
    articleSummary.hidden=!articleSummary.textContent;
    const message=status==='error'?'Не удалось загрузить новость. Закройте окно и повторите загрузку в разделе новостей.':'Возможно, новость снята с публикации или ссылка устарела.';
    articleBody.replaceChildren(...renderArticleBody(article?article.body:status==='loading'?'Подождите, пожалуйста.':message));
    dialog.setAttribute('aria-busy',String(status==='loading'));
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
  tag.textContent=String(item.category||'Новость').toUpperCase()+(item.featured?' · Закреплено':'');
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

function publicLink(value){
  if(!value)return '';
  try{
    const url=new URL(value,window.location.origin);
    return ['http:','https:'].includes(url.protocol)?url.href:'';
  }catch(error){return '';}
}

function renderHomepageBlocks(payload){
  if(importantBlock){
    const item=payload.announcement;
    if(!item){
      importantBlock.replaceChildren(...originalImportant.map(node=>node.cloneNode(true)));
    }else{
      const copy=document.createElement('div');
      copy.className='notice-copy';
      const label=document.createElement('div');
      label.className='notice-kicker';
      label.textContent=item.label||'';
      const title=document.createElement('h1');
      title.id='important-title';
      title.textContent=item.title;
      const subtitle=document.createElement('p');
      subtitle.className='notice-lead';
      subtitle.textContent=item.subtitle||'';
      const body=document.createElement('div');
      body.className='notice-text';
      body.append(...renderArticleBody(item.body));
      copy.append(label,title,subtitle,body);
      const href=publicLink(item.button_url);
      if(href&&item.button_text){
        const actions=document.createElement('div');
        actions.className='notice-actions';
        const button=document.createElement('a');
        button.className='primary';
        button.href=href;
        button.textContent=item.button_text;
        actions.append(button);
        copy.append(actions);
      }
      importantBlock.replaceChildren(copy);
      const imageUrl=publicLink(item.image_url);
      if(imageUrl&&new URL(imageUrl).origin===window.location.origin){
        const image=document.createElement('img');
        image.src=imageUrl;
        image.alt='';
        image.style.cssText='max-width:100%;height:auto;border-radius:12px';
        importantBlock.append(image);
      }
    }
  }
  if(contactsBlock&&contactsContent){
    const item=payload.contacts;
    contactsBlock.hidden=!item&&originalContacts.length===0;
    contactsContent.replaceChildren(...originalContacts.map(node=>node.cloneNode(true)));
    if(item){
      contactsContent.replaceChildren();
      for(const [key,label] of [['email','Почта'],['phone','Телефон'],['address','Адрес'],['office_hours','Часы приёма'],['how_to_contact','Как связаться']]){
        if(!item[key])continue;
        const paragraph=document.createElement('p');
        paragraph.style.whiteSpace='pre-line';
        const strong=document.createElement('strong');
        strong.textContent=label+': ';
        paragraph.append(strong,document.createTextNode(item[key]));
        contactsContent.append(paragraph);
      }
    }
  }
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
  articleLoads.clear();
  newsGrid.setAttribute('aria-busy','true');
  documentsList.setAttribute('aria-busy','true');
  newsGrid.replaceChildren(contentState('Загружаем новости…'));
  documentsList.replaceChildren(contentState('Загружаем документы…'));
  syncArticleFromUrl();
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const preview=document.querySelector('#public-preview-data');
    let payload;
    if(preview){
      payload=JSON.parse(preview.textContent);
    }else{
      const response=await fetch('/admin/public/content/',{headers:{Accept:'application/json'},credentials:'same-origin',signal:controller.signal});
      if(!response.ok)throw new Error(`HTTP ${response.status}`);
      payload=await response.json();
    }
    if(!Array.isArray(payload.news)||!Array.isArray(payload.documents))throw new Error('Invalid public content');
    renderHomepageBlocks(payload);
    const forumLink=document.getElementById('forum-link');
    if(forumLink)forumLink.hidden=payload.forum_url!=='/admin/cabinet/forum/';
    articles=payload.news.slice();
    if(payload.article&&!articles.some(item=>item.id===payload.article.id))articles.push(payload.article);
    contentStatus='ready';
    newsGrid.replaceChildren(...(payload.news.length?payload.news.map(newsCard):[contentState('Пока нет опубликованных новостей.')]));
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
