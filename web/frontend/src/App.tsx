import { ChangeEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';

type Evidence = { id?: string; kind: string; detail?: string; summary?: string; timeframe?: string; source?: string; engine?: string };
type ValidationResult = { valid?: boolean; issues?: string[] };
type TimeframeState = { bias?: string; structure?: string; displacement?: { grade?: string }; bos?: unknown; choch?: unknown };
type Decision = { symbol?: string; direction?: string; directional_bias?: string; decision_state?: string; confidence?: number; entry?: number; stop_loss?: number; take_profits?: number[]; expected_rr?: number; reason?: string; entry_status?: string; setup_zone?: { low: number; high: number; source: string }; data_diagnostic?: DataDiagnostics; scores?: Record<string, number>; structure?: Record<string, TimeframeState>; setups?: Array<{ type: string; timeframe: string; state?: string }>; supporting_evidence?: Evidence[]; contradicting_evidence?: Evidence[]; conflicts?: string[]; limitations?: string[]; extension_chase?: { detected?: boolean }; synthesis_mode?: string; validation_result?: ValidationResult; cache?: string };
type DataQuality = { critical_missing?: string[]; critical_stale?: string[]; optional_missing?: string[]; optional_stale?: string[] };
type DataDiagnostics = { quality?: DataQuality; blockers?: string[]; reason?: string };
type Analysis = { id?: string; decision?: Decision; synthesis_mode?: string; cache?: string; validation_result?: ValidationResult; verified_state?: { evidence?: Evidence[]; limitations?: string[] } };
type Message = { id: string; role: 'user' | 'assistant'; content: string; image?: string; decision?: Decision; analysis?: Analysis; streaming?: boolean; analysisStage?: string; diagnostics?: DataDiagnostics };
type Conversation = { id: string; title: string; active_symbol?: string | null; updated_at: string };
type StreamEvent = { stage?: string; text?: string; decision?: Decision; reason?: string; quality?: DataQuality; blockers?: string[] };

const prompts = ['Analyze BTCUSDT across multiple timeframes', 'Build a risk-managed ETHUSDT swing trade plan', 'Compare SOLUSDT and ETHUSDT momentum', 'Show my paper trading performance'];
const stageLabels: Record<string, string> = {
  fetching_market_data: 'Fetching live market data',
  validating_market_data: 'Validating market data',
  loading_4h_structure: 'Loading 4h market structure',
  loading_1h_structure: 'Loading 1h market structure',
  loading_30m_structure: 'Loading 30m market structure',
  loading_15m_structure: 'Loading 15m market structure',
  loading_5m_structure: 'Loading 5m market structure',
  analyzing_market: 'Analyzing market structure',
  analyzing_smc: 'Analyzing SMC structure',
  analyzing_liquidity: 'Analyzing liquidity',
  analyzing_futures_flow: 'Analyzing futures flow',
  loading_historical_context: 'Loading historical context',
  assembling_verified_state: 'Assembling verified market state',
  reasoning_with_llm: 'Reasoning over market state',
  validating_decision: 'Validating paper decision',
  validation_failure: 'Market data validation failed',
};

async function chatResponseError(response: Response) {
  const body = (await response.text()).trim();
  if (!body) return `Chat service returned HTTP ${response.status}.`;
  try { const parsed = JSON.parse(body) as { detail?: string }; return parsed.detail || `Chat service returned HTTP ${response.status}.`; }
  catch { return `${response.status} ${response.statusText}: ${body.slice(0, 240)}`; }
}

function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string>();
  const [draft, setDraft] = useState(''); const [image, setImage] = useState<string>();
  const [streaming, setStreaming] = useState(false); const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false); const aborter = useRef<AbortController | null>(null); const fileInput = useRef<HTMLInputElement>(null); const end = useRef<HTMLDivElement>(null);
  useEffect(() => { void loadConversations(true); }, []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streaming]);

  async function loadConversations(selectLatest = false) { try { const response = await fetch('/api/conversations'); if (!response.ok) throw new Error('Unable to load conversations.'); const items = await response.json() as Conversation[]; setConversations(items); if (selectLatest && !conversationId && items[0]) await selectConversation(items[0].id); } catch { setError('The conversation service is unavailable.'); } }
  async function createConversation(clearMessages = false) { const response = await fetch('/api/conversations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: 'New conversation' }) }); if (!response.ok) throw new Error('Unable to create a conversation.'); const created = await response.json() as Conversation; setConversations(items => [created, ...items]); setConversationId(created.id); if (clearMessages) setMessages([]); return created.id; }
  async function selectConversation(id: string) {
    try {
      const response = await fetch(`/api/conversations/${id}`);
      if (!response.ok) throw new Error('Unable to load conversation.');
      const data = await response.json() as { messages: Array<{ id: string; role: 'user' | 'assistant'; content: string; metadata?: { analysis?: Analysis } }> };
      const restored = data.messages.map(item => ({ ...item, analysis: item.metadata?.analysis, decision: item.metadata?.analysis?.decision }));
      if (!restored.some(item => item.analysis)) {
        const latest = await fetch(`/api/conversations/${id}/latest-analysis`);
        if (latest.ok) { const analysis = await latest.json() as Analysis; const target = [...restored].reverse().find(item => item.role === 'assistant'); if (target) { target.analysis = analysis; target.decision = analysis.decision; } }
      }
      setConversationId(id); setMessages(restored); setSidebarOpen(false);
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to load conversation.'); }
  }
  function stopReply() { aborter.current?.abort(); setStreaming(false); setMessages(items => items.map(item => item.streaming ? { ...item, streaming: false } : item)); }
  async function sendMessage(value = draft) {
    const question = value.trim(); if (!question || streaming) return;
    setDraft(''); setImage(undefined); setError(null); setStreaming(true);
    const user: Message = { id: crypto.randomUUID(), role: 'user', content: question, image }; const assistantId = crypto.randomUUID();
    setMessages(items => [...items, user, { id: assistantId, role: 'assistant', content: '', streaming: true }]);
    try {
      const id = conversationId || await createConversation(); const controller = new AbortController(); aborter.current = controller;
      const response = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify({ conversation_id: id, message: question }), signal: controller.signal });
      if (!response.ok) throw new Error(await chatResponseError(response));
      if (!response.body) throw new Error('The chat connection opened without a response stream. Please retry after the deployment is healthy.');
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('text/event-stream')) throw new Error(await chatResponseError(response));
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      while (true) { const { value, done } = await reader.read(); if (done) break; buffer += decoder.decode(value, { stream: true }); const blocks = buffer.split('\n\n'); buffer = blocks.pop() || ''; blocks.forEach(block => consumeEvent(block, assistantId)); }
      setMessages(items => items.map(item => item.id === assistantId ? { ...item, streaming: false, content: item.content || 'No response was returned.' } : item)); await loadConversations();
    } catch (err) { if ((err as Error).name !== 'AbortError') { const message = err instanceof Error ? err.message : 'Unable to reach chat service.'; setError(message); setMessages(items => items.map(item => item.id === assistantId ? { ...item, streaming: false, content: `Unable to complete this request. ${message}` } : item)); } }
    finally { setStreaming(false); aborter.current = null; }
  }
  function consumeEvent(block: string, assistantId: string) {
    const type = block.match(/^event: (.+)$/m)?.[1]; const raw = block.match(/^data: (.+)$/m)?.[1]; if (!raw) return;
    try {
      const data = JSON.parse(raw) as StreamEvent;
      setMessages(items => items.map(item => {
        if (item.id !== assistantId) return item;
        if ((type === 'status' || type === 'analysis_stage') && data.stage) return { ...item, analysisStage: data.stage };
        if (type === 'market_data') return { ...item, diagnostics: { ...item.diagnostics, quality: data.quality } };
        if (type === 'validation_failure') return { ...item, analysisStage: data.stage || 'validation_failure', diagnostics: { ...item.diagnostics, blockers: data.blockers, reason: data.reason } };
        if (type === 'partial_text') return { ...item, content: data.text || item.content };
        if (type === 'analysis_result' && data.decision) return { ...item, decision: data.decision, analysis: (data as StreamEvent & { analysis?: Analysis }).analysis || item.analysis, diagnostics: { ...item.diagnostics, ...data.decision.data_diagnostic, reason: data.decision.reason || item.diagnostics?.reason } };
        if (type === 'analysis_result' && data.reason) return { ...item, content: item.content || data.reason };
        return item;
      }));
    } catch { /* Ignore malformed SSE frames without losing the stream. */ }
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void sendMessage(); } }
  function pickImage(event: ChangeEvent<HTMLInputElement>) { const file = event.target.files?.[0]; if (file?.type.startsWith('image/')) setImage(URL.createObjectURL(file)); event.target.value = ''; }
  return <div className="workspace"><aside className={`sidebar ${sidebarOpen ? 'open' : ''}`} aria-label="Chat history"><div className="sidebar-brand"><span className="brand-orb">△</span><span>Orion</span><small>TRADING AI</small></div><button className="new-chat" onClick={() => void createConversation(true)}><span>＋</span> New chat</button><nav className="chat-nav"><p>RECENT</p>{conversations.map(chat => <button key={chat.id} className={chat.id === conversationId ? 'active' : ''} onClick={() => void selectConversation(chat.id)}>{chat.title}</button>)}</nav><div className="sidebar-footer"><span>Paper-only market intelligence</span></div></aside>{sidebarOpen && <button className="scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}<main className="conversation"><header className="workspace-header"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}>☰</button><div className="header-title"><strong>Trading workspace</strong><span><i /> Persistent chat and live market context</span></div></header><section className={`message-scroll ${messages.length ? 'has-messages' : ''}`} aria-live="polite">{messages.length === 0 ? <EmptyWorkspace onPrompt={setDraft} /> : messages.map(message => <MessageBubble key={message.id} message={message} />)}{error && <p className="inline-error">{error}</p>}<div ref={end} /></section><Composer draft={draft} image={image} streaming={streaming} onChange={setDraft} onKeyDown={keyDown} onSend={() => void sendMessage()} onStop={stopReply} onAttach={() => fileInput.current?.click()} onRemoveImage={() => setImage(undefined)} /><input ref={fileInput} className="visually-hidden" type="file" accept="image/*" onChange={pickImage} /></main></div>;
}
function EmptyWorkspace({ onPrompt }: { onPrompt: (value: string) => void }) { return <div className="empty-workspace"><div className="hero-mark">⌁</div><p className="eyebrow">CONVERSATIONAL MARKET INTELLIGENCE</p><h1>See the market<br /><em>before it moves.</em></h1><p className="hero-copy">Ask for a thesis, a setup, or a risk-defined paper trade plan.</p><div className="prompt-grid">{prompts.map(prompt => <button key={prompt} onClick={() => onPrompt(prompt)}><span>{prompt}</span><b>↗</b></button>)}</div></div>; }
function MessageBubble({ message }: { message: Message }) { const stage = message.analysisStage && stageLabels[message.analysisStage]; const validationFailed = message.analysisStage === 'validation_failure' || message.decision?.direction === 'DATA_UNAVAILABLE'; return <article className={`message ${message.role}`}><div className="avatar">{message.role === 'user' ? 'YOU' : '△'}</div><div className="message-body"><div className="message-label">{message.role === 'user' ? 'YOU' : 'ORION'} {message.streaming && <span className="typing">thinking</span>}</div>{stage && <div className={`analysis-stage ${message.analysisStage === 'validation_failure' ? 'failure' : ''}`}><span className="stage-dot" />{stage}</div>}{message.image && <img className="message-image" src={message.image} alt="Attached market chart" />}<p>{message.content || ' '}{message.streaming && <span className="cursor" />}</p>{validationFailed && message.diagnostics && <DataDiagnostics diagnostics={message.diagnostics} />}{message.decision && <AnalysisCard decision={message.decision} analysis={message.analysis} />}</div></article>; }
function DataDiagnostics({ diagnostics }: { diagnostics: DataDiagnostics }) { const quality = diagnostics.quality; const groups = [['Critical missing', quality?.critical_missing], ['Critical stale', quality?.critical_stale], ['Optional missing', quality?.optional_missing], ['Optional stale', quality?.optional_stale]] as const; const hasQuality = groups.some(([, values]) => values?.length); if (!diagnostics.reason && !diagnostics.blockers?.length && !hasQuality) return null; return <aside className="data-diagnostics" aria-label="Market data diagnostics"><b>MARKET DATA DIAGNOSTICS</b>{diagnostics.reason && <span>{diagnostics.reason}</span>}{diagnostics.blockers?.length ? <small>Blocked by: {diagnostics.blockers.join(', ')}</small> : null}<div>{groups.filter(([, values]) => values?.length).map(([label, values]) => <small key={label}>{label}: {values?.join(', ')}</small>)}</div></aside>; }
function AnalysisCard({ decision, analysis }: { decision: Decision; analysis?: Analysis }) { const unavailable = decision.decision_state === 'DATA_UNAVAILABLE' || decision.direction === 'DATA_UNAVAILABLE'; const waiting = !unavailable && decision.decision_state?.endsWith('_WAIT'); const states = Object.entries(decision.structure || {}); const evidence = analysis?.verified_state?.evidence || []; const limitations = analysis?.verified_state?.limitations || decision.limitations; return <section className={`analysis-card ${unavailable ? 'unavailable' : ''}`}><div className="card-top"><div><span>{unavailable ? 'MARKET DATA STATUS' : 'DECISION ENGINE V3 · PAPER ONLY'}</span><h2>{unavailable ? 'MARKET DATA UNAVAILABLE' : decision.symbol || 'MARKET'} {!unavailable && <small>{decision.decision_state || 'NO_TRADE'}</small>}</h2></div>{!unavailable && <div className="confidence"><b>{Math.round((decision.confidence || 0) * 100)}%</b><span>OVERALL</span></div>}</div><div className="direction"><span>{unavailable ? 'Analysis status' : 'Final state / directional bias'}</span><strong>{unavailable ? 'Market data unavailable' : `${decision.decision_state || 'NO_TRADE'} · ${decision.directional_bias || 'NEUTRAL'}`}</strong><small>{decision.reason}</small>{decision.synthesis_mode && <small>Synthesis: {decision.synthesis_mode.replace('_', ' ')} · Cache: {decision.cache || 'miss'}</small>}{decision.validation_result?.issues?.length ? <small>Validator issues: {decision.validation_result.issues.join(', ')}</small> : null}</div>{decision.scores && <div className="score-grid">{Object.entries(decision.scores).map(([name, value]) => <div key={name}><span>{name}</span><b>{Math.round(value)}</b></div>)}</div>}{states.length > 0 && <div className="timeframes">{states.map(([timeframe, state]) => <div key={timeframe}><b>{timeframe}</b><span>{state.bias || 'neutral'} · {state.structure || '—'}</span><small>{state.displacement?.grade || 'NONE'} {state.bos ? 'BOS' : state.choch ? 'CHOCH' : ''}</small></div>)}</div>}{waiting && decision.setup_zone && <div className="levels"><div><span>SETUP ZONE</span><b>{decision.setup_zone.low} – {decision.setup_zone.high}</b></div><div><span>READINESS</span><b>{decision.entry_status}</b></div></div>}{!unavailable && decision.entry_status === 'CONFIRMED' && decision.entry !== undefined && <div className="levels"><div><span>ENTRY</span><b>{decision.entry}</b></div><div><span>STOP LOSS</span><b className="danger">{decision.stop_loss}</b></div><div><span>TARGETS</span><b className="profit">{decision.take_profits?.join(' / ')}</b></div></div>}{(decision.setups?.length || decision.supporting_evidence?.length || decision.conflicts?.length) ? <div className="evidence"><b>SETUPS & EVIDENCE</b>{decision.setups?.slice(0, 4).map((setup, i) => <span key={`s${i}`}>{setup.timeframe} {setup.type}{setup.state ? ` · ${setup.state}` : ''}</span>)}{decision.supporting_evidence?.slice(0, 2).map((item, i) => <span key={`e${i}`}>{item.detail}</span>)}{decision.conflicts?.map((item, i) => <span className="conflict" key={`c${i}`}>Conflict: {item}</span>)}</div> : null}{evidence.length ? <div className="evidence"><b>VALIDATED EVIDENCE</b><span>Supporting: {evidence.slice(0, 2).map(item => item.detail || item.summary || item.kind).join(' · ')}</span>{decision.conflicts?.length ? <span className="conflict">Contradicting: {decision.conflicts.slice(0, 2).join(' · ')}</span> : null}</div> : null}{decision.extension_chase?.detected && <p className="invalidation"><b>CHASE FILTER:</b> Price is extended; wait for a defendable pullback.</p>}{limitations?.length ? <p className="invalidation"><b>LIMITATIONS:</b> {limitations[0]}</p> : null}</section>; }
function Composer({ draft, image, streaming, onChange, onKeyDown, onSend, onStop, onAttach, onRemoveImage }: { draft: string; image?: string; streaming: boolean; onChange: (value: string) => void; onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void; onSend: () => void; onStop: () => void; onAttach: () => void; onRemoveImage: () => void }) { return <div className="composer-wrap">{image && <div className="image-preview"><img src={image} alt="Attachment preview" /><button onClick={onRemoveImage} aria-label="Remove attachment">×</button></div>}<div className="composer"><button className="attach-button" onClick={onAttach} aria-label="Attach chart image">＋</button><textarea value={draft} rows={1} placeholder="Ask Orion about the market…" onChange={event => onChange(event.target.value)} onKeyDown={onKeyDown} aria-label="Message Orion" />{streaming ? <button className="stop-button" onClick={onStop} aria-label="Stop generating"><span /></button> : <button className="send-button" onClick={onSend} disabled={!draft.trim()} aria-label="Send message">↑</button>}</div><p className="composer-hint">Enter to send <span>·</span> Shift + Enter for new line</p></div>; }
export default App;
