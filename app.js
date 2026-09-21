document.querySelectorAll('dialog').forEach(d=>{d.querySelector('.close').addEventListener('click',()=>d.close());d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}})});

const dialog=document.querySelector('#news-dialog');
const articleTitle=document.querySelector('#article-title');
const articleBody=document.querySelector('#article-body');
const newsGrid=document.querySelector('#news-grid');
const documentsList=document.querySelector('#documents-list');

const fallbackArticles=[
  {title:'Важное объявление',body:'Раздел готовится к наполнению. После утверждения здесь будут публиковаться важные сообщения товарищества.'},
  {title:'Новости товарищества',body:'Публикации о выполненных работах, общих планах и событиях появятся в этом разделе.'},
  {title:'Полезная информация',body:'Здесь будут размещаться инструкции, напоминания и ответы на частые вопросы жителей.'}
];

function formatDate(value){
  const [year,month,day]=String(value||'').split('-');
  return year&&month&&day?`${day}.${month}.${year}`:'';
}

function openArticle(article){
  articleTitle.textContent=article.title;
  const paragraphs=String(article.body||'').split(/\n\s*\n/).map(v=>v.trim()).filter(Boolean);
  articleBody.replaceChildren(...paragraphs.map(text=>{const p=document.createElement('p');p.textContent=text;return p;}));
  dialog.showModal();
}

function maybeOpenFeatured(items){
  const featured=items.find(item=>item&&item.featured);
  if(!featured||dialog.open)return;
  const key=`trud1-featured-news:${featured.id}`;
  try{
    if(sessionStorage.getItem(key))return;
    sessionStorage.setItem(key,'1');
  }catch(error){
    console.warn('Не удалось сохранить отметку о показе срочной новости.',error);
  }
  window.setTimeout(()=>{if(!dialog.open)openArticle(featured);},150);
}

function wireFallback(){
  document.querySelectorAll('[data-news]').forEach(button=>button.addEventListener('click',()=>openArticle(fallbackArticles[Number(button.dataset.news)])));
}

function newsCard(item,index){
  const article=document.createElement('article');
  article.className=`news-card${item.featured?' featured':''}`;

  const meta=document.createElement('div');
  meta.className='card-meta';
  const tag=document.createElement('span');
  tag.className='tag';
  tag.textContent=String(item.category||'НОВОСТЬ').toUpperCase();
  const date=document.createElement('span');
  date.textContent=formatDate(item.date);
  meta.append(tag,date);

  const marker=document.createElement('div');
  marker.className=item.featured?'card-symbol':'number';
  marker.setAttribute('aria-hidden','true');
  marker.textContent=item.featured?'↗':String(index+1).padStart(2,'0');

  const title=document.createElement('h3');
  title.textContent=item.title;
  const summary=document.createElement('p');
  summary.textContent=item.summary;
  const button=document.createElement('button');
  button.className='read';
  button.type='button';
  button.textContent='Подробнее →';
  button.addEventListener('click',()=>openArticle(item));
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
  meta.textContent=`${formatDate(item.date)} · открыть`;
  link.append(main,meta);
  return link;
}

async function loadPublicContent(){
  try{
    const response=await fetch('/admin/public/content/',{headers:{Accept:'application/json'},credentials:'same-origin'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const payload=await response.json();
    if(Array.isArray(payload.news)&&payload.news.length){
      newsGrid.replaceChildren(...payload.news.map(newsCard));
      maybeOpenFeatured(payload.news);
    }
    if(Array.isArray(payload.documents)&&payload.documents.length){
      documentsList.replaceChildren(...payload.documents.map(documentRow));
    }
  }catch(error){
    console.warn('Публичный контент временно недоступен; показан безопасный статический вариант.',error);
  }
}

wireFallback();
loadPublicContent();
