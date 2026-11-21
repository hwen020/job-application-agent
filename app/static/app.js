'use strict';
const node = id => document.getElementById(id);
let selected;
let parsed;
let task;
let busy = false;

function element(name, content) {
    const item = document.createElement(name);
    if (content) item.textContent = content;
    return item;
}

function button(label, action) {
    const item = element('button', label);
    item.onclick = () => perform(action);
    return item;
}

async function api(path, payload) {
    const response = await fetch('/api/'+path, payload===undefined ? {} : {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error);
    return value;
}

async function perform(action) {
    if (busy) return;
    busy = true;
    node('message').textContent = 'Working…';
    try {
        await action();
        node('message').textContent = '';
    } catch (error) {
        node('message').textContent = error.message;
    } finally {
        busy = false;
    }
}

function renderList(target, lines) {
    target.replaceChildren(...lines.map(line => element('li',line)));
}

async function refresh() {
    const data = await api('dashboard');
    const metrics = [['Openings',data.reports.openings],['Approved versions',data.reports.approved_versions],
        ['Mock receipts',data.reports.simulated_receipts]];
    node('reports').replaceChildren(...metrics.map(([label,value]) => {
        const card = element('div');
        card.className = 'metric';
        card.append(element('strong',String(value)),element('span',label));
        return card;
    }));
    node('jobs').replaceChildren(...data.jobs.map(job => {
        const row = element('div');
        row.className = 'job';
        const text = element('div');
        text.append(element('strong',job.title),element('small',job.employer+' · Mock '+job.platform+' · '+job.status));
        row.append(text,button('Open job & parse JD',() => openJob(job)));
        return row;
    }));
    renderList(node('audit'),data.audit.map(record => record.action.replaceAll('_',' ')+(record.detail?' · '+record.detail:'')));
    renderList(node('providers'),data.providers.map(record => record.provider+' · '+record.item));
}

async function openJob(job) {
    selected = job;
    parsed = await api('demo/parse',{job_id:job.id});
    task = null;
    node('workflow').hidden = false;
    node('jobTitle').textContent = job.title;
    node('jd').textContent = parsed.text;
    node('materials').replaceChildren();
    node('runner').replaceChildren();
    const matching = node('matching');
    const score = parsed.coverage;
    const evidence = element('ul');
    renderList(evidence,parsed.jd.analysis.requirements.map(requirement => {
        const match = parsed.matches.find(item=>item.requirement_id===requirement.id);
        return requirement.text+' · '+requirement.level+' · '+match.state.replaceAll('_',' ');
    }));
    const checks = element('ul');
    const records = parsed.eligibility_checks;
    renderList(checks, Object.entries(records).map(([key,value]) => key.replaceAll('_',' ')+
        ' · '+(typeof value==='object' ? (value.state || value.status || 'review') : value)));
    matching.replaceChildren(element('h3','Matching & eligibility'),
        element('p',score.with_evidence+' of '+score.total+' requirements have linked evidence.'),evidence,checks);
    node('facts').replaceChildren(...parsed.candidates.map((fact,index) => {
        const label = element('label');
        const choice = element('input');
        choice.type = 'checkbox';
        choice.value = fact.fact_id;
        choice.checked = index<2;
        const sources = fact.sources.map(source => source.name+' '+source.locator).join(', ');
        label.append(choice,document.createTextNode(' '+fact.statement+' · '+sources));
        return label;
    }));
    const existing = await api('demo/job/'+job.id);
    if (existing.material) showMaterials(existing.material);
    if (existing.task) {
        task = existing.task;
        showRunner();
    }
    await refresh();
    node('workflow').scrollIntoView();
}

function showMaterials(version) {
    const panel = node('materials');
    const prefix = '/api/artifacts/'+version.id+'/';
    const links = element('div');
    links.className = 'downloads';
    for (const filename of ['Resume.pdf','Resume.docx','Cover_Letter.pdf','Cover_Letter.docx']) {
        const link = element('a',filename);
        link.href = prefix+filename;
        link.download = filename;
        links.append(link);
    }
    const preview = element('iframe');
    preview.src = prefix+'Resume.pdf';
    preview.title = 'Synthetic Demo resume preview';
    panel.replaceChildren(element('h3',version.approved_at?'Approved materials':'Review generated materials'),links,preview);
    if (!version.approved_at) panel.append(button('Approve materials', async () => {
        if (!confirm('Approve this Synthetic Demo resume and cover letter?')) return;
        showMaterials(await api('demo/approve',{version_id:version.id,confirmed:true}));
        await refresh();
    }));
    else if (!task) panel.append(button('Enter Mock '+selected.platform, async () => {
        task = await api('demo/tasks',{job_id:selected.id});
        showRunner();
        node('runner').scrollIntoView();
    }));
}

async function run(action) {
    const payload = {task_id:task.task_id,action};
    if (action==='submit') {
        if (!confirm('Generate a Synthetic Demo receipt for the reviewed mock form?')) return;
        payload.confirmed = true;
        payload.review_token = task.review_token;
    }
    task = await api('demo/control',payload);
    showRunner();
    await refresh();
}

function showRunner() {
    const panel = node('runner');
    const checkpoint = task.checkpoint;
    panel.replaceChildren(element('h3','Application Runner · Mock '+selected.platform),element('p',task.state),
        element('p',checkpoint.message || ''));
    if (task.submitted_receipt) panel.append(element('strong',task.submitted_receipt));
    if (checkpoint.completed?.length) {
        const list = element('ul');
        renderList(list,checkpoint.completed.map(field => field.question+' · '+field.answer));
        panel.append(list);
    }
    for (const field of (checkpoint.pending || []).filter(field=>field.confirmable)) {
        const label = element('label', field.question+' ');
        const select = element('select');
        for (const option of field.options) {
            const item = element('option',option.label);
            item.value = option.id;
            select.append(item);
        }
        const confirmAnswer = button('Confirm answer',async () => {
            await api('demo/answer',{task_id:task.task_id,field_id:field.id,answer:select.value,confirmed:true});
            confirmAnswer.textContent = 'Answer saved';
            confirmAnswer.disabled = true;
            select.disabled = true;
        });
        label.append(select,document.createTextNode(' '),confirmAnswer);
        panel.append(label);
    }
    const actions = element('div');
    actions.className = 'actions';
    if (task.state==='READY_FOR_REVIEW') actions.append(button('Confirm mock submission',()=>run('submit')));
    if (!['SUBMITTED','CANCELLED'].includes(task.state)) {
        actions.append(button(task.state==='CREATED'?'Fill mock form':'Resume from checkpoint',()=>run('run')));
        if (task.state!=='PAUSED') actions.append(button('Pause',()=>run('pause')));
    }
    panel.append(actions);
    const log = element('details');
    log.append(element('summary','Runner history'));
    const events = element('ul');
    renderList(events,task.events.map(event=>event.action+' · '+event.detail));
    log.append(events);
    panel.append(log);
}

node('generate').onclick = () => perform(async () => {
    const fact_ids = [...node('facts').querySelectorAll('input:checked')].map(field=>Number(field.value));
    const version = await api('demo/generate',{job_id:selected.id,jd_id:parsed.jd.id,fact_ids});
    showMaterials(version);
    node('materials').scrollIntoView();
    await refresh();
});
node('reset').onclick = () => perform(async () => {
    if (!confirm('Reset all Synthetic Demo progress for this instance?')) return;
    await api('demo/reset',{confirmed:true});
    node('workflow').hidden = true;
    await refresh();
});
perform(refresh);
