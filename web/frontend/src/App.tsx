import { ChangeEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';

type RunStatus = 'queued' | 'running' | 'completed' | 'failed';
type MarketDataVendor = 'yfinance' | 'binance_spot' | 'binance_futures';

type FormState = {
  ticker: string; analysis_date: string; use_latest_date: boolean; asset_type: 'stock' | 'crypto';
  market_data_vendor: MarketDataVendor; binance_interval: string; binance_quote_asset: string;
  llm_provider: string; backend_url: string; quick_think_llm: string; deep_think_llm: string;
  api_key: string; output_language: string; analysts: string[]; max_debate_rounds: number;
  max_risk_discuss_rounds: number; checkpoint_enabled: boolean;
};

type RunDetail = { run_id: string; status: RunStatus; ticker: string; report?: string | null; decision?: string | null; error?: string | null };
type Message = { id: string; role: 'user' | 'assistant'; content: string; image?: string; card?: boolean; streaming?: boolean };

const today = () => new Date().toISOString().slice(0, 10);
const defaultForm: FormState = {
  ticker: 'APRUSDT', analysis_date: today(), use_latest_date: true, asset_type: 'crypto', market_data_vendor: 'binance_futures', binance_interval: '4h', binance_quote_asset: 'USDT', llm_provider: 'openai_compatible', backend_url: 'https://gemini.salesmanchatbot.online/v1', quick_think_llm: 'gemini-3.6-flash', deep_think_llm: 'gemini-3.6-flash', api_key: '', output_language: 'English', analysts: ['market', 'social', 'news'], max_debate_rounds: 1, max_risk_discuss_rounds: 1, checkpoint_enabled: false,
};
const prompts = [
  'Analyze APRUSDT across multiple timeframes',
  'Build a risk-managed BTC swing trade plan',
  'What are the strongest altcoin setups today?',
  'Compare SOL and ETH momentum for this week',
];
const recentChats = ['APRUSDT multi-timeframe setup', 'BTC risk-managed swing plan', 'SOL momentum breakout', 'ETH / BTC relative strength'];

function App() {
  const [form, setForm] = useState(defaultForm);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [image, setImage] = useState<string>();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [useBackend, setUseBackend] = useState(false);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const streamTimer = useRef<number | null>(null);
  const messagesEnd = useRef<HTMLDivElement>(null);

  useEffect(() => { messagesEnd.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streaming]);
  useEffect(() => () => { if (streamTimer.current) window.clearInterval(streamTimer.current); }, []);
  useEffect(() => {
    if (!run?.run_id || run.status === 'completed' || run.status === 'failed') return;
    const interval = window.setInterval(async () => {
      try {
        const response = await fetch(`/api/runs/${run.run_id}`);
        if (!response.ok) return;
        const next = await response.json() as RunDetail;
        setRun(next);
        if (next.status === 'completed') finishBackendReply(next);
        if (next.status === 'failed') { setError(next.error || 'The analysis run failed.'); setStreaming(false); }
      } catch { /* Backend availability should not block the workspace. */ }
    }, 3000);
    return () => window.clearInterval(interval);
  }, [run?.run_id, run?.status]);

  function newChat() { stopReply(); setMessages([]); setDraft(''); setImage(undefined); setError(null); setSidebarOpen(false); }
  function stopReply() { if (streamTimer.current) window.clearInterval(streamTimer.current); streamTimer.current = null; setStreaming(false); setMessages((items) => items.map((item) => item.streaming ? { ...item, streaming: false, content: item.content || 'Analysis paused.' } : item)); }
  function update<K extends keyof FormState>(key: K, value: FormState[K]) { setForm((current) => ({ ...current, [key]: value })); }

  function mockReply(question: string) {
    const response = `APRUSDT is showing constructive structure after reclaiming its 4H trend pivot. Momentum is strongest on the 1H and 4H charts, while the daily trend is still building confirmation.\n\nThe preferred approach is to wait for controlled pullbacks into the entry zone rather than chase an extended move. Keep size modest until price accepts above the first target.`;
    let cursor = 0;
    const assistantId = crypto.randomUUID();
    setMessages((items) => [...items, { id: assistantId, role: 'assistant', content: '', card: /APR|trade|setup|analy/i.test(question), streaming: true }]);
    streamTimer.current = window.setInterval(() => {
      cursor += 5;
      setMessages((items) => items.map((item) => item.id === assistantId ? { ...item, content: response.slice(0, cursor), streaming: cursor < response.length } : item));
      if (cursor >= response.length) { if (streamTimer.current) window.clearInterval(streamTimer.current); streamTimer.current = null; setStreaming(false); }
    }, 22);
  }

  async function sendMessage(value = draft) {
    const question = value.trim();
    if (!question || streaming) return;
    setMessages((items) => [...items, { id: crypto.randomUUID(), role: 'user', content: question, image }]);
    setDraft(''); setImage(undefined); setError(null); setStreaming(true);
    if (!useBackend) { mockReply(question); return; }
    try {
      const response = await fetch('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...form, ticker: detectTicker(question) || form.ticker, analysis_date: form.use_latest_date ? today() : form.analysis_date, api_key: form.api_key || null, backend_url: form.backend_url || null }) });
      if (!response.ok) throw new Error(await response.text());
      const data = await response.json() as { run_id: string };
      setRun({ run_id: data.run_id, status: 'queued', ticker: detectTicker(question) || form.ticker });
      setMessages((items) => [...items, { id: crypto.randomUUID(), role: 'assistant', content: 'I’m collecting market context and running the configured analyst workflow…', streaming: true }]);
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to start the backend run.'); setStreaming(false); mockReply(question); }
  }

  function finishBackendReply(result: RunDetail) {
    setStreaming(false);
    setMessages((items) => items.map((item) => item.streaming ? { ...item, streaming: false, card: true, content: result.report || result.decision || 'Analysis completed. Review the trade plan below.' } : item));
  }
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendMessage(); } }
  function pickImage(event: ChangeEvent<HTMLInputElement>) { const file = event.target.files?.[0]; if (file?.type.startsWith('image/')) setImage(URL.createObjectURL(file)); event.target.value = ''; }

  return <div className="workspace">
    <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`} aria-label="Chat history">
      <div className="sidebar-brand"><span className="brand-orb">△</span><span>Orion</span><small>TRADING AI</small></div>
      <button className="new-chat" onClick={newChat}><span>＋</span> New chat</button>
      <nav className="chat-nav"><p>RECENT</p>{recentChats.map((chat, index) => <button key={chat} className={index === 0 && messages.length ? 'active' : ''} onClick={() => { setDraft(`Continue: ${chat}`); setSidebarOpen(false); }}>{chat}</button>)}</nav>
      <nav className="chat-nav saved"><p>SAVED</p><button onClick={() => setDraft('Show my saved APRUSDT trade plan')}>APRUSDT watchlist</button><button onClick={() => setDraft('Summarize saved BTC plan')}>BTC swing framework</button></nav>
      <div className="sidebar-footer"><button onClick={() => setSettingsOpen(true)}>Settings</button><span>Workspace v1.0</span></div>
    </aside>
    {sidebarOpen && <button className="scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
    <main className="conversation">
      <header className="workspace-header">
        <button className="icon-button mobile-menu" aria-label="Open navigation" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)}>☰</button>
        <div className="header-title"><strong>Trading workspace</strong><span><i /> Live market context</span></div>
        <div className="header-actions"><span className="model-pill">{form.deep_think_llm}</span><button className="icon-button" aria-label="Open settings" onClick={() => setSettingsOpen(true)}>⚙</button></div>
      </header>
      <section className={`message-scroll ${messages.length ? 'has-messages' : ''}`} aria-live="polite">
        {messages.length === 0 ? <EmptyWorkspace onPrompt={setDraft} /> : messages.map((message) => <MessageBubble key={message.id} message={message} />)}
        {error && <p className="inline-error">{error}</p>}<div ref={messagesEnd} />
      </section>
      <Composer draft={draft} image={image} streaming={streaming} onChange={setDraft} onKeyDown={handleKeyDown} onSend={() => sendMessage()} onStop={stopReply} onAttach={() => fileInput.current?.click()} onRemoveImage={() => setImage(undefined)} />
      <input ref={fileInput} className="visually-hidden" type="file" accept="image/*" onChange={pickImage} />
    </main>
    {settingsOpen && <Settings form={form} useBackend={useBackend} onClose={() => setSettingsOpen(false)} onUpdate={update} onUseBackend={setUseBackend} />}
  </div>;
}

function EmptyWorkspace({ onPrompt }: { onPrompt: (value: string) => void }) { return <div className="empty-workspace"><div className="hero-mark">⌁</div><p className="eyebrow">CONVERSATIONAL MARKET INTELLIGENCE</p><h1>See the market<br /><em>before it moves.</em></h1><p className="hero-copy">Ask for a thesis, a multi-timeframe setup, or a risk-defined trade plan. Orion brings the market context together.</p><div className="prompt-grid">{prompts.map((prompt) => <button key={prompt} onClick={() => onPrompt(prompt)}><span>{prompt}</span><b>↗</b></button>)}</div></div>; }
function MessageBubble({ message }: { message: Message }) { return <article className={`message ${message.role}`}><div className="avatar">{message.role === 'user' ? 'YO' : '△'}</div><div className="message-body"><div className="message-label">{message.role === 'user' ? 'YOU' : 'ORION'} {message.streaming && <span className="typing">thinking</span>}</div>{message.image && <img className="message-image" src={message.image} alt="Attached market chart" />}<p>{message.content || ' '}{message.streaming && <span className="cursor" />}</p>{message.card && !message.streaming && <AnalysisCard />}</div></article>; }
function AnalysisCard() { return <section className="analysis-card"><div className="card-top"><div><span>TRADE BLUEPRINT</span><h2>APRUSDT <small>PERP</small></h2></div><div className="confidence"><b>78%</b><span>CONFIDENCE</span></div></div><div className="direction"><span>Directional bias</span><strong>LONG <i>↑</i></strong><small>4H trend continuation</small></div><div className="mtf"><span>MTF alignment</span><div>{[['15m','Bullish'],['1H','Bullish'],['4H','Bullish'],['1D','Neutral']].map(([frame, state]) => <p key={frame}><b>{frame}</b><i className={state.toLowerCase()} />{state}</p>)}</div></div><div className="levels"><div><span>ENTRY ZONE</span><b>$0.472–0.481</b></div><div><span>STOP LOSS</span><b className="danger">$0.452</b></div><div><span>TAKE PROFITS</span><b className="profit">$0.505 · $0.532</b></div></div><p className="invalidation"><b>Invalidation</b> · 4H close below $0.452 or loss of rising volume structure.</p></section>; }
function Composer({ draft, image, streaming, onChange, onKeyDown, onSend, onStop, onAttach, onRemoveImage }: { draft: string; image?: string; streaming: boolean; onChange: (value: string) => void; onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void; onSend: () => void; onStop: () => void; onAttach: () => void; onRemoveImage: () => void }) { return <div className="composer-wrap">{image && <div className="image-preview"><img src={image} alt="Attachment preview" /><button onClick={onRemoveImage} aria-label="Remove attachment">×</button></div>}<div className="composer"><button className="attach-button" onClick={onAttach} aria-label="Attach chart image">＋</button><textarea value={draft} rows={1} placeholder="Ask Orion about the market…" onChange={(event) => onChange(event.target.value)} onKeyDown={onKeyDown} aria-label="Message Orion" />{streaming ? <button className="stop-button" onClick={onStop} aria-label="Stop generating"><span /></button> : <button className="send-button" onClick={onSend} disabled={!draft.trim()} aria-label="Send message">↑</button>}</div><p className="composer-hint">Enter to send <span>·</span> Shift + Enter for new line</p></div>; }
function Settings({ form, useBackend, onClose, onUpdate, onUseBackend }: { form: FormState; useBackend: boolean; onClose: () => void; onUpdate: <K extends keyof FormState>(key: K, value: FormState[K]) => void; onUseBackend: (value: boolean) => void }) { return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" onMouseDown={(event) => event.stopPropagation()}><header><div><p>WORKSPACE CONFIGURATION</p><h2 id="settings-title">Advanced settings</h2></div><button onClick={onClose} aria-label="Close settings">×</button></header><div className="settings-content"><label className="toggle-row"><span><b>Use live backend runs</b><small>POST /api/runs and poll GET /api/runs/:id</small></span><input type="checkbox" checked={useBackend} onChange={(event) => onUseBackend(event.target.checked)} /></label><div className="setting-grid"><Field label="Ticker / Pair"><input value={form.ticker} onChange={(e) => onUpdate('ticker', e.target.value)} /></Field><Field label="Market source"><select value={form.market_data_vendor} onChange={(e) => onUpdate('market_data_vendor', e.target.value as MarketDataVendor)}><option value="binance_futures">Binance Futures</option><option value="binance_spot">Binance Spot</option><option value="yfinance">Yahoo Finance</option></select></Field><Field label="Interval"><select value={form.binance_interval} onChange={(e) => onUpdate('binance_interval', e.target.value)}>{['15m','1h','4h','1d'].map((x) => <option key={x}>{x}</option>)}</select></Field><Field label="Output language"><input value={form.output_language} onChange={(e) => onUpdate('output_language', e.target.value)} /></Field><Field label="Provider" wide><input value={form.llm_provider} onChange={(e) => onUpdate('llm_provider', e.target.value)} /></Field><Field label="Model" wide><input value={form.deep_think_llm} onChange={(e) => onUpdate('deep_think_llm', e.target.value)} /></Field><Field label="Base URL" wide><input value={form.backend_url} onChange={(e) => onUpdate('backend_url', e.target.value)} /></Field><Field label="API key" wide><input type="password" value={form.api_key} placeholder="Sent only with a live run" onChange={(e) => onUpdate('api_key', e.target.value)} /></Field></div></div><footer><button className="secondary" onClick={onClose}>Cancel</button><button className="save-settings" onClick={onClose}>Save settings</button></footer></section></div>; }
function Field({ label, wide, children }: { label: string; wide?: boolean; children: React.ReactNode }) { return <label className={wide ? 'wide' : ''}><span>{label}</span>{children}</label>; }
function detectTicker(input: string) { return input.match(/\b[A-Z]{2,}(?:USDT|USD|BTC|ETH)\b/i)?.[0]?.toUpperCase(); }
export default App;
