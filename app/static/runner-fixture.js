const transfer = document.querySelector('[data-next]');
if (transfer) transfer.onclick = () => {
    const values = JSON.parse(sessionStorage.getItem('simulation-fields') || '{}');
    for (const item of document.querySelectorAll('main input,main select')) {
        const saved = item.type === 'file' ? item.files[0]?.name : item.value;
        if (saved) values[item.id] = saved;
    }
    sessionStorage.setItem('simulation-fields', JSON.stringify(values));
};
const button = document.querySelector('#send');
if (button) {
    for (const [id,value] of Object.entries(JSON.parse(sessionStorage.getItem('simulation-fields') || '{}'))) {
        const summary = document.createElement('div');
        summary.dataset.answerId = id;
        summary.textContent = value;
        button.before(summary);
    }
    button.onclick = async () => {
        button.disabled = true;
        const key = location.pathname.split('/').pop();
        const response = await fetch('/api/runner/mock-receipt', {method:'POST',
            headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
        if (!response.ok) { button.disabled = false; return; }
        const {receipt} = await response.json();
        const output = document.createElement('div');
        output.dataset.receipt = '';
        output.textContent = receipt;
        button.after(output);
    };
}
