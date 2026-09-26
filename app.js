document.querySelectorAll('dialog').forEach(d=>{d.querySelector('.close').addEventListener('click',()=>d.close());d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}})});

const dialog=document.querySelector('#news-dialog');
const articleMeta=document.querySelector('#article-meta');
const articleTitle=document.querySelector('#article-title');
const articleSummary=document.querySelector('#article-summary');
const articleBody=document.querySelector('#article-body');
const newsGrid=document.querySelector('#news-grid');
const documentsList=document.querySelector('#documents-list');

const fallbackArticles=[
  {
    id:'land-2026-09-21',
    title:'Участок ваш. А документы с вами согласны?',
    category:'ЗЕМЛЯ И ПРАВА',
    date:'2026-09-21',
    featured:true,
    summary:'Что на самом деле означает 1 января 2027 года и кому разумно проверить оформление земли заранее.',
    body:`Дом стоит.
Забор стоит.
Соседи знают, где ваш участок.

Иногда кажется, что этого достаточно.

Пока не понадобилось продать землю, оформить наследство, уточнить границы или доказать право документами.

Сейчас много разговоров о 1 января 2027 года. По действующему российскому законодательству до этой даты в Крыму могут устанавливаться специальные особенности регулирования земельных и имущественных отношений, кадастрового учёта и государственной регистрации недвижимости. Сама эта дата не означает автоматического прекращения права на участок и не устанавливает обязанность всем собственникам заново оформлять землю.

Есть ещё одна важная деталь. Раньше для садоводческих и огороднических товариществ в Крыму действительно существовал срок до 1 сентября 2026 года для подачи заявления о предоставлении земли в упрощённом порядке. В мае 2026 года это ограничение было отменено Законом Республики Крым № 193-ЗРК/2026.

Поэтому паниковать из-за одной даты не нужно.

> А проверить документы — разумно.

Особенно если:

- документы старые
- оформление когда-то начали и не закончили
- границы участка вызывают вопросы
- забор и документы рассказывают немного разные истории
- вы просто не знаете, что именно сейчас записано о вашем участке в государственных реестрах

Если право оформлено и сведения актуальны, специально переоформлять участок только потому, что наступает 1 января 2027 года, из рассмотренной нормы не следует.

Если есть неясность — лучше разобраться заранее.

Не потому что «завтра всё отнимут».

А потому что документы имеют неприятную привычку вспоминать о себе в самый неудобный момент: при продаже, наследстве, строительстве или споре с соседом.

> Земля может переходить от человека к человеку. Хорошо, когда документы переходят вместе с ней.

ТСН «Труд-1» будет следить за изменениями законодательства и рассказывать о них спокойно: что действительно изменилось, кого это касается и что имеет смысл делать.

Материал информационный. Конкретная ситуация зависит от документов каждого участка.

Актуально на 21 сентября 2026 года.

Нормативная основа: часть 1.1 статьи 12.1 Федерального конституционного закона № 6-ФКЗ; Закон Республики Крым № 320-ЗРК/2016 с изменениями; Закон Республики Крым от 28.05.2026 № 193-ЗРК/2026.`
  },
  {
    id:'memory-100-years',
    title:'Зачем этому сайту быть через сто лет?',
    category:'МЕСТО И ПАМЯТЬ',
    date:'',
    featured:false,
    summary:'Не про вечность. Про документы, схемы, решения и знания, которые слишком легко исчезают вместе с людьми.',
    body:`Сайт товарищества обычно нужен для очень сегодняшних вещей: узнать новость, посмотреть документ, передать показания, понять, когда будет вода.

Но у сегодняшнего есть одна особенность. Оно довольно быстро становится прошлым.

Сегодня мы ремонтируем дорогу — через двадцать лет никто уже не помнит, почему её сделали именно так. Сегодня кто-то знает, где проходит старая труба, — завтра этот человек уезжает. Сегодня в ящике лежит схема участка — через поколение никто не понимает, откуда она взялась.

Поэтому у сайта Труд-1 две работы.

Первая — помогать жить сейчас.

Вторая — не давать полезному знанию исчезать.

Документы, схемы инфраструктуры, история решений, старые фотографии и карты имеют смысл только тогда, когда понятно, откуда они взялись и насколько им можно доверять.

Не нужно писать «на века». Достаточно писать так, чтобы через двадцать лет не пришлось начинать расследование заново.

> Сегодняшняя информация помогает жить. Сохранённая — помогает не начинать всё сначала.`
  },
  {
    id:'water-memory',
    title:'Воду замечают дважды: когда её проводят и когда её нет',
    category:'КАК ВСЁ УСТРОЕНО',
    date:'',
    featured:false,
    summary:'Пока вода течёт, труба невидима. Поэтому карту сети и историю ремонтов лучше собирать до следующей аварии.',
    body:`Пока вода течёт из крана, труба почти невидима.

Она становится очень заметной, когда случается авария. Тогда внезапно выясняется, насколько важны простые вопросы: где проходит линия, какого она диаметра, когда её меняли, где задвижка, какой участок сети самый старый и кто помнит, что делали здесь двадцать лет назад.

Память одного человека — плохая инженерная документация.

Поэтому одна из задач Труд-1 — постепенно собрать карту водоснабжения и историю сети: схемы, узлы, ремонты, счётчики, фотографии работ и известные проблемные места.

Не ради красивой картинки.

Чтобы следующую аварию искать быстрее. Чтобы ремонт планировать до разрушения. Чтобы новое правление не начинало расследование заново.

Если у вас сохранились старые схемы, фотографии прокладки сети, записи о ремонтах или документы, связанные с водой Труд-1, их важно сохранить. После проверки они могут стать частью технического архива товарищества.

> Вода должна течь по трубам. Знание о трубах — переходить дальше.`
  }
];

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

function openArticle(article){
  const meta=[String(article.category||'НОВОСТЬ').toUpperCase(),formatDate(article.date)].filter(Boolean);
  articleMeta.textContent=meta.join(' · ');
  articleTitle.textContent=article.title;
  articleSummary.textContent=article.summary||'';
  articleSummary.hidden=!article.summary;
  articleBody.replaceChildren(...renderArticleBody(article.body));
  dialog.scrollTop=0;
  dialog.showModal();
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
