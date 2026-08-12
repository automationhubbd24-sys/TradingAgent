import { FormEvent, ReactNode, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';

type RunStatus = 'queued' | 'running' | 'completed' | 'failed';
type MarketDataVendor = 'yfinance' | 'binance_spot' | 'binance_futures';
type ThemeMode = 'dark' | 'light';

type RunDetail = {
  run_id: string;
  status: RunStatus;
  ticker: string;
  analysis_date: string;
  created_at: string;
  completed_at?: string | null;
  error?: string | null;
  decision?: string | null;
  report?: string | null;
  report_path?: string | null;
  sections: Record<string, string>;
};

type FormState = {
  ticker: string;
  analysis_date: string;
  use_latest_date: boolean;
  asset_type: 'stock' | 'crypto';
  market_data_vendor: MarketDataVendor;
  binance_interval: string;
  binance_quote_asset: string;
  llm_provider: string;
  backend_url: string;
  quick_think_llm: string;
  deep_think_llm: string;
  api_key: string;
  output_language: string;
  analysts: string[];
  max_debate_rounds: number;
  max_risk_discuss_rounds: number;
  checkpoint_enabled: boolean;
};

type FormFieldProps = {
  label: string;
  children: ReactNode;
  className?: string;
  hint?: string;
};

type AnalyzeSetupProps = {
  form: FormState;
  run?: RunDetail | null;
  error: string | null;
  submitting: boolean;
  isBinance: boolean;
  selectedAnalysts: Set<string>;
  mobileSetupOpen: boolean;
  onSubmit: (event: FormEvent) => void;
  onFieldChange: <K extends keyof FormState>(key: K, value: FormState[K]) => void;
  onVendorChange: (value: MarketDataVendor) => void;
  onAnalystToggle: (value: string) => void;
  onToggleMobileSetup: () => void;
  onRetry?: () => void;
};

type RunResultProps = {
  run: RunDetail | null;
  error: string | null;
  isBusy: boolean;
  onRetry: () => void;
};

const API_BASE = '';
const analystOptions = [
  { value: 'market', label: 'Market' },
  { value: 'social', label: 'Sentiment' },
  { value: 'news', label: 'News' },
  { value: 'fundamentals', label: 'Fundamentals' },
];
const binanceVendors: MarketDataVendor[] = ['binance_spot', 'binance_futures'];
const today = () => new Date().toISOString().slice(0, 10);

const defaultForm: FormState = {
  ticker: 'NVDA',
  analysis_date: today(),
  use_latest_date: true,
  asset_type: 'stock',
  market_data_vendor: 'yfinance',
  binance_interval: '1d',
  binance_quote_asset: 'USDT',
  llm_provider: 'openai_compatible',
  backend_url: 'https://gemini.salesmanchatbot.online/v1',
  quick_think_llm: 'gemini-3.6-flash',
  deep_think_llm: 'gemini-3.6-flash',
  api_key: '',
  output_language: 'English',
  analysts: ['market', 'social', 'news', 'fundamentals'],
  max_debate_rounds: 1,
  max_risk_discuss_rounds: 1,
  checkpoint_enabled: false,
};

function App() {
  const [form, setForm] = useState<FormState>(defaultForm);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [theme, setTheme] = useState<ThemeMode>('dark');
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [mobileSetupOpen, setMobileSetupOpen] = useState(false);

  const isBinance = binanceVendors.includes(form.market_data_vendor);
  const isBusy = submitting || run?.status === 'queued' || run?.status === 'running';
  const canPoll = runId && run?.status !== 'completed' && run?.status !== 'failed';

  useEffect(() => {
    if (!canPoll || !runId) return;
    const timer = window.setInterval(() => {
      fetchRun(runId);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [canPoll, runId]);

  const selectedAnalysts = useMemo(() => new Set(form.analysts), [form.analysts]);

  async function fetchRun(id: string) {
    const response = await fetch(`${API_BASE}/api/runs/${id}`);
    if (!response.ok) {
      setError(`Run status আনতে সমস্যা হয়েছে: ${response.status}`);
      return;
    }
    setRun(await response.json());
  }

  async function startRun(event?: FormEvent) {
    event?.preventDefault();
    if (isBusy) return;
    setSubmitting(true);
    setError(null);
    setRun(null);
    setRunId(null);

    try {
      const response = await fetch(`${API_BASE}/api/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          analysis_date: form.use_latest_date ? today() : form.analysis_date,
          api_key: form.api_key || null,
          backend_url: form.backend_url || null,
        }),
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Request failed: ${response.status}`);
      }

      const data = await response.json();
      setRunId(data.run_id);
      await fetchRun(data.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setSubmitting(false);
    }
  }

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateVendor(value: MarketDataVendor) {
    setForm((current) => {
      const next: FormState = { ...current, market_data_vendor: value };
      if (binanceVendors.includes(value)) {
        next.asset_type = 'crypto';
        next.analysts = current.analysts.filter((analyst) => analyst !== 'fundamentals');
        if (current.ticker === 'NVDA') next.ticker = 'BTCUSDT';
      }
      return next;
    });
  }

  function toggleAnalyst(value: string) {
    if ((isBinance || form.asset_type === 'crypto') && value === 'fundamentals') return;
    setForm((current) => {
      const selected = new Set(current.analysts);
      if (selected.has(value)) selected.delete(value);
      else selected.add(value);
      return { ...current, analysts: Array.from(selected) };
    });
  }

  return (
    <main className="app-shell" data-theme={theme}>
      <ResponsiveContainer>
        <Header
          status={run?.status || 'idle'}
          theme={theme}
          mobileNavOpen={mobileNavOpen}
          onToggleTheme={() => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))}
          onToggleMobileNav={() => setMobileNavOpen((current) => !current)}
        />

        <section className="page-heading">
          <p className="eyebrow">TradingAgents Web MVP</p>
          <h1>Market Analysis</h1>
          <p>Stock, crypto, Binance spot/futures analysis</p>
        </section>

        <section className="analysis-grid">
          <AnalyzeSetup
            form={form}
            run={run}
            error={error}
            submitting={submitting}
            isBinance={isBinance}
            selectedAnalysts={selectedAnalysts}
            mobileSetupOpen={mobileSetupOpen}
            onSubmit={startRun}
            onFieldChange={updateField}
            onVendorChange={updateVendor}
            onAnalystToggle={toggleAnalyst}
            onToggleMobileSetup={() => setMobileSetupOpen((current) => !current)}
          />
          <RunResult run={run} error={error} isBusy={isBusy} onRetry={() => startRun()} />
        </section>

        <footer className="app-footer">© 2026 TradingAgents Web MVP. All rights reserved.</footer>
      </ResponsiveContainer>
    </main>
  );
}

function ResponsiveContainer({ children }: { children: ReactNode }) {
  return <div className="container">{children}</div>;
}

function Header({
  status,
  theme,
  mobileNavOpen,
  onToggleTheme,
  onToggleMobileNav,
}: {
  status: RunStatus | 'idle';
  theme: ThemeMode;
  mobileNavOpen: boolean;
  onToggleTheme: () => void;
  onToggleMobileNav: () => void;
}) {
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark">TA</div>
        <div>
          <strong>TradingAgents</strong>
          <span>WEB MVP</span>
        </div>
      </div>

      <button
        className="hamburger"
        type="button"
        aria-label="Toggle navigation"
        aria-expanded={mobileNavOpen}
        onClick={onToggleMobileNav}
      >
        <span />
        <span />
        <span />
      </button>

      <nav className={`main-nav ${mobileNavOpen ? 'open' : ''}`}>
        <a href="#analysis">Analyze</a>
        <a href="#history">History</a>
        <a href="#reports">Saved Reports</a>
        <a href="#settings">Settings</a>
      </nav>

      <div className="topbar-actions">
        <AnalysisStatus status={status} compact />
        <button className="theme-toggle" type="button" onClick={onToggleTheme}>
          {theme === 'dark' ? 'Light' : 'Dark'}
        </button>
      </div>
    </header>
  );
}

function FormField({ label, children, className = '', hint }: FormFieldProps) {
  return (
    <label className={`form-field ${className}`}>
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

function AnalyzeSetup({
  form,
  run,
  error,
  submitting,
  isBinance,
  selectedAnalysts,
  mobileSetupOpen,
  onSubmit,
  onFieldChange,
  onVendorChange,
  onAnalystToggle,
  onToggleMobileSetup,
}: AnalyzeSetupProps) {
  const disabled = submitting || run?.status === 'running' || run?.status === 'queued';
  return (
    <form id="analysis" className={`panel setup-panel ${mobileSetupOpen ? 'mobile-open' : ''}`} onSubmit={onSubmit}>
      <button
        className="mobile-panel-toggle"
        type="button"
        aria-expanded={mobileSetupOpen}
        onClick={onToggleMobileSetup}
      >
        <PanelHeader title="Analyze Setup" subtitle="Configure symbol, market source and AI model." />
      </button>
      <div className="desktop-panel-heading">
        <PanelHeader title="Analyze Setup" subtitle="Configure symbol, market source and AI model." />
      </div>

      <div className="field-group setup-section-market">
        <div className="section-label">Market</div>
        <div className="form-grid">
          <FormField label="Ticker / Pair">
            <input value={form.ticker} onChange={(e) => onFieldChange('ticker', e.target.value)} placeholder="BTCUSDT" />
          </FormField>
          <FormField label="Analysis Date">
            <input
              type="date"
              disabled={form.use_latest_date}
              value={form.use_latest_date ? today() : form.analysis_date}
              onChange={(e) => onFieldChange('analysis_date', e.target.value)}
            />
          </FormField>
          <label className="switch-row full-row">
            <input
              type="checkbox"
              checked={form.use_latest_date}
              onChange={(e) =>
                onFieldChange('use_latest_date', e.target.checked)
              }
            />
            <span>Use latest/current date automatically</span>
          </label>
          <FormField label="Asset Type">
            <select value={form.asset_type} onChange={(e) => onFieldChange('asset_type', e.target.value as FormState['asset_type'])}>
              <option value="stock">Stock</option>
              <option value="crypto">Crypto</option>
            </select>
          </FormField>
          <FormField label="Market Data Vendor">
            <select value={form.market_data_vendor} onChange={(e) => onVendorChange(e.target.value as MarketDataVendor)}>
              <option value="yfinance">Yahoo Finance</option>
              <option value="binance_spot">Binance Spot</option>
              <option value="binance_futures">Binance USD-M Futures</option>
            </select>
          </FormField>
          {isBinance && (
            <>
              <FormField label="Binance Interval">
                <select value={form.binance_interval} onChange={(e) => onFieldChange('binance_interval', e.target.value)}>
                  <option value="1m">1m</option>
                  <option value="5m">5m</option>
                  <option value="15m">15m</option>
                  <option value="1h">1h</option>
                  <option value="4h">4h</option>
                  <option value="1d">1d</option>
                </select>
              </FormField>
              <FormField label="Quote Asset">
                <input value={form.binance_quote_asset} onChange={(e) => onFieldChange('binance_quote_asset', e.target.value)} />
              </FormField>
              <p className="inline-note full-row">Examples: BTCUSDT, SOLUSDT, 1000PEPEUSDT, WIFUSDT, BTC/USDT:USDT.</p>
            </>
          )}
        </div>
      </div>

      <div className="field-group setup-section-ai">
        <div className="section-label">AI Provider</div>
        <div className="form-grid">
          <FormField label="Provider">
            <input value={form.llm_provider} onChange={(e) => onFieldChange('llm_provider', e.target.value)} />
          </FormField>
          <FormField label="Output Language">
            <input value={form.output_language} onChange={(e) => onFieldChange('output_language', e.target.value)} />
          </FormField>
          <FormField label="Base URL" className="full-row">
            <input value={form.backend_url} onChange={(e) => onFieldChange('backend_url', e.target.value)} />
          </FormField>
          <FormField label="API Key" className="full-row" hint="The key is sent to the backend for this run only and is not saved to disk.">
            <input type="password" value={form.api_key} onChange={(e) => onFieldChange('api_key', e.target.value)} placeholder="Paste API key" />
          </FormField>
          <FormField label="Quick Model">
            <input value={form.quick_think_llm} onChange={(e) => onFieldChange('quick_think_llm', e.target.value)} />
          </FormField>
          <FormField label="Deep Model">
            <input value={form.deep_think_llm} onChange={(e) => onFieldChange('deep_think_llm', e.target.value)} />
          </FormField>
        </div>
      </div>

      <div className="field-group compact-group setup-section-execution">
        <div className="section-label">Execution Settings</div>
        <div className="form-grid tight">
          <FormField label="Debate Rounds">
            <input type="number" min="1" value={form.max_debate_rounds} onChange={(e) => onFieldChange('max_debate_rounds', Number(e.target.value))} />
          </FormField>
          <FormField label="Risk Rounds">
            <input type="number" min="1" value={form.max_risk_discuss_rounds} onChange={(e) => onFieldChange('max_risk_discuss_rounds', Number(e.target.value))} />
          </FormField>
        </div>
      </div>

      <div className="analyst-block">
        <div className="section-label">Analysts</div>
        <div className="analyst-list">
          {analystOptions.map((item) => {
            const disabledAnalyst = (isBinance || form.asset_type === 'crypto') && item.value === 'fundamentals';
            return (
              <label key={item.value} className={`chip-check ${disabledAnalyst ? 'disabled' : ''}`}>
                <input type="checkbox" disabled={disabledAnalyst} checked={!disabledAnalyst && selectedAnalysts.has(item.value)} onChange={() => onAnalystToggle(item.value)} />
                <span>{item.label}</span>
              </label>
            );
          })}
        </div>
      </div>

      <label className="switch-row checkpoint-row">
        <input type="checkbox" checked={form.checkpoint_enabled} onChange={(e) => onFieldChange('checkpoint_enabled', e.target.checked)} />
        <span>Checkpoint enabled</span>
      </label>

      <button className="primary-action" disabled={disabled}>
        {disabled ? 'Analysis in Progress' : 'Start Analysis'}
      </button>
      {error && !run?.error && <p className="form-error">{error}</p>}
    </form>
  );
}

function PanelHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="panel-header">
      <span className="panel-icon" aria-hidden="true">⌁</span>
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
      <span className="panel-caret" aria-hidden="true">⌄</span>
    </div>
  );
}

function AnalysisStatus({ status, compact = false }: { status: RunStatus | 'idle'; compact?: boolean }) {
  return (
    <div className={`analysis-status ${status} ${compact ? 'compact' : ''}`}>
      <span className="status-dot" />
      <span>{status === 'idle' ? 'Ready' : status}</span>
    </div>
  );
}

function RunResult({ run, error, isBusy, onRetry }: RunResultProps) {
  if (!run && !error && !isBusy) return <EmptyState />;
  if (isBusy && (!run || run.status === 'queued' || run.status === 'running')) return <LoadingState run={run} />;
  if ((run?.status === 'failed' && run.error) || error) return <ErrorState message={run?.error || error || 'Analysis failed'} onRetry={onRetry} run={run} />;
  if (!run) return <EmptyState />;

  const decision = extractDecision(run);
  const reportSections = buildReportSections(run);

  return (
    <section className="panel result-panel" id="reports">
      <div className="result-card-inner">
        <div className="result-topline">
          <PanelHeader title="Run Result" subtitle="AI-generated financial analysis report." />
          <AnalysisStatus status={run.status} />
        </div>

        <div className="run-meta-grid">
          <MetaItem label="Run ID" value={shortId(run.run_id)} />
          <MetaItem label="Asset / Pair" value={run.ticker} />
          <MetaItem label="Analysis Date" value={run.analysis_date} />
          <MetaItem label="Completed" value={formatDateTime(run.completed_at || run.created_at)} />
        </div>

        <section className="decision-card decision-strip">
          <span>Decision</span>
          <strong>{decision}</strong>
        </section>

        <div className="report-title-row report-summary">
          <div>
            <h3>Trading Analysis Report: {run.ticker}</h3>
            <p>Generated {formatDateTime(run.completed_at || run.created_at)}</p>
          </div>
        </div>

        <div className="accordion-list">
          {reportSections.map((section, index) => (
            <ReportSection key={`${section.title}-${index}`} title={section.title} defaultOpen={index === 0}>
              <ReactMarkdown>{section.content}</ReactMarkdown>
            </ReportSection>
          ))}
        </div>

        <ReportActions run={run} />
      </div>
    </section>
  );
}

function EmptyState() {
  return (
    <section className="panel result-panel empty-panel">
      <div className="empty-illustration">AI</div>
      <h2>Ready for Analysis</h2>
      <p>Configure your analysis parameters and start an analysis.</p>
      <div className="empty-hints">
        <span>Binance Futures</span>
        <span>Live Date</span>
        <span>AI Report</span>
      </div>
    </section>
  );
}

function LoadingState({ run }: { run: RunDetail | null }) {
  return (
    <section className="panel result-panel loading-panel">
      <div className="loading-orb" />
      <AnalysisStatus status={run?.status || 'running'} />
      <h2>Analysis in progress</h2>
      <p>{currentStage(run?.status)}. This can take a little while while agents collect data and produce the report.</p>
      <div className="progress-track">
        <span />
      </div>
      {run && (
        <div className="run-meta-grid compact-grid">
          <MetaItem label="Run ID" value={shortId(run.run_id)} />
          <MetaItem label="Asset / Pair" value={run.ticker} />
          <MetaItem label="Date" value={run.analysis_date} />
        </div>
      )}
    </section>
  );
}

function ErrorState({ message, onRetry, run }: { message: string; onRetry: () => void; run: RunDetail | null }) {
  return (
    <section className="panel result-panel error-panel">
      <AnalysisStatus status="failed" />
      <h2>Analysis failed</h2>
      <p className="error-message">{message}</p>
      {run && (
        <div className="run-meta-grid compact-grid">
          <MetaItem label="Run ID" value={shortId(run.run_id)} />
          <MetaItem label="Asset / Pair" value={run.ticker} />
          <MetaItem label="Date" value={run.analysis_date} />
        </div>
      )}
      <button className="secondary-action" type="button" onClick={onRetry}>Retry Analysis</button>
    </section>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReportSection({ title, children, defaultOpen }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(Boolean(defaultOpen));
  return (
    <article className={`report-section ${open ? 'open' : ''}`}>
      <button
        type="button"
        className="report-section-trigger"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span>{title}</span>
        <span>{open ? '⌃' : '⌄'}</span>
      </button>
      {open && <div className="report-section-body markdown">{children}</div>}
    </article>
  );
}

function ReportActions({ run }: { run: RunDetail }) {
  async function copyReport() {
    await navigator.clipboard.writeText(run.report || run.decision || '');
  }

  function saveReport() {
    const blob = new Blob([run.report || run.decision || ''], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${run.ticker}_${run.analysis_date}_report.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="report-actions">
      <button className="report-action-pdf" type="button" onClick={() => window.print()}>Download PDF</button>
      <button className="report-action-copy" type="button" onClick={copyReport}>Copy Report</button>
      <button className="report-action-save" type="button" onClick={saveReport}>Save Report</button>
    </div>
  );
}

function extractDecision(run: RunDetail) {
  const source = `${run.decision || ''}\n${run.report || ''}`;
  const explicit = source.match(/(?:FINAL TRANSACTION PROPOSAL|Recommendation|Decision|Action)\s*:?\s*\**\s*([A-Z][A-Z\s/-]{2,})/i);
  return explicit?.[1]?.replace(/\*/g, '').trim().toUpperCase() || run.decision?.trim().slice(0, 48).toUpperCase() || 'COMPLETED';
}

function buildReportSections(run: RunDetail) {
  if (run.sections && Object.keys(run.sections).length > 0) {
    return Object.entries(run.sections).map(([key, value]) => ({ title: titleCase(key), content: value }));
  }

  const report = run.report || run.decision || 'No report content available.';
  const sections = splitMarkdownSections(report);
  if (sections.length > 1) return sections;

  return [
    { title: 'Market Overview', content: report },
    { title: 'Technical Analysis', content: 'Included in the generated report when available.' },
    { title: 'Support & Resistance', content: 'Included in the generated report when available.' },
    { title: 'Risk Factors', content: 'Included in the generated report when available.' },
    { title: 'Conclusion', content: run.decision || 'Final decision is included in the generated report.' },
  ];
}

function splitMarkdownSections(markdown: string) {
  const chunks = markdown.split(/\n(?=#{1,3}\s+)/g).filter(Boolean);
  return chunks.slice(0, 8).map((chunk, index) => {
    const firstLine = chunk.split('\n')[0];
    const title = firstLine.replace(/^#{1,3}\s+/, '').replace(/[*_`]/g, '').trim() || `Report Section ${index + 1}`;
    return { title, content: chunk };
  });
}

function titleCase(value: string) {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function shortId(value: string) {
  return value.length > 14 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function currentStage(status?: RunStatus) {
  if (status === 'queued') return 'Queued for analysis';
  return 'Collecting market data and generating agent analysis';
}

export default App;
