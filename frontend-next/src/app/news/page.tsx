"use client";

import { useEffect, useState, useCallback, useRef } from "react";

const C = {
  bg: '#0d1117',
  card: '#161b22',
  border: '#30363d',
  text: '#e6edf3',
  muted: '#8b949e',
  accent: '#58a6ff',
  green: '#22c55e',
  red: '#ef4444',
  yellow: '#f0a500',
  purple: '#a371f7',
  orange: '#fb923c',
};

const IMPORTANCE_COLOR: Record<string, string> = {
  critical: C.red,
  high:     C.orange,
  medium:   C.yellow,
  low:      C.muted,
};

const CATEGORY_TABS = [
  { label: "ALL",     value: undefined },
  { label: "CRYPTO",  value: "crypto" },
  { label: "FOREX",   value: "forex" },
  { label: "STOCKS",  value: "stock" },
  { label: "GENERAL", value: "general" },
];

const IMPORTANCE_TABS = [
  { label: "All",      value: undefined },
  { label: "Medium+",  value: "medium+" },
  { label: "High+",    value: "high+" },
  { label: "Critical", value: "critical" },
];

interface NewsItem {
  id: number;
  headline: string;
  summary: string | null;
  source: string | null;
  url: string | null;
  published_at: string | null;
  sentiment_score: number | null;
  importance: string;
  category: string | null;
}

function stripHtml(html: string): string {
  return html.replace(/<[^>]*>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, " ").trim();
}

function sentimentLabel(score: number | null): { label: string; color: string } {
  if (score == null) return { label: "—", color: C.muted };
  if (score > 0.05)  return { label: "▲ BULL", color: C.green };
  if (score < -0.05) return { label: "▼ BEAR", color: C.red };
  return { label: "— NEUT", color: C.muted };
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function NewsCard({ item }: { item: NewsItem }) {
  const [expanded, setExpanded] = useState(false);
  const sent = sentimentLabel(item.sentiment_score);
  const impColor = IMPORTANCE_COLOR[item.importance] ?? C.muted;

  return (
    <div
      style={{
        borderBottom: `1px solid ${C.border}`,
        padding: '14px 16px',
        cursor: item.summary ? 'pointer' : 'default',
        transition: 'background 0.1s',
      }}
      onClick={() => item.summary && setExpanded((e) => !e)}
    >
      {/* Top row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>

        {/* Importance dot */}
        <div style={{
          marginTop: 5, flexShrink: 0,
          width: 7, height: 7, borderRadius: '50%',
          background: impColor,
        }} />

        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Headline */}
          {item.url ? (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              style={{
                fontSize: 13, fontWeight: 600, color: C.text,
                textDecoration: 'none', lineHeight: 1.5,
                display: 'block',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.color = C.accent)}
              onMouseLeave={(e) => (e.currentTarget.style.color = C.text)}
            >
              {item.headline}
            </a>
          ) : (
            <span style={{ fontSize: 13, fontWeight: 600, color: C.text, lineHeight: 1.5 }}>
              {item.headline}
            </span>
          )}

          {/* Meta row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 5, flexWrap: 'wrap' }}>
            {item.source && (
              <span style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
                {item.source.replace(/ - .*/, '').replace(/\.com.*/, '')}
              </span>
            )}
            <span style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace' }}>
              {fmtTime(item.published_at)}
            </span>
            {item.category && (
              <span style={{
                fontSize: 10, fontWeight: 600, fontFamily: 'monospace',
                color: C.accent, background: `${C.accent}18`,
                border: `1px solid ${C.accent}30`,
                borderRadius: 3, padding: '1px 5px',
              }}>
                {item.category.toUpperCase()}
              </span>
            )}
            <span style={{
              fontSize: 10, fontWeight: 700, fontFamily: 'monospace',
              color: impColor, background: `${impColor}18`,
              border: `1px solid ${impColor}30`,
              borderRadius: 3, padding: '1px 5px',
            }}>
              {item.importance.toUpperCase()}
            </span>
            <span style={{ fontSize: 11, fontWeight: 600, fontFamily: 'monospace', color: sent.color }}>
              {sent.label}
              {item.sentiment_score != null && (
                <span style={{ color: C.muted, fontWeight: 400 }}>
                  {' '}({item.sentiment_score > 0 ? '+' : ''}{item.sentiment_score.toFixed(2)})
                </span>
              )}
            </span>
          </div>
        </div>

        {/* Expand arrow */}
        {item.summary && (
          <span style={{ color: C.muted, fontSize: 11, flexShrink: 0, marginTop: 3 }}>
            {expanded ? '▲' : '▼'}
          </span>
        )}
      </div>

      {/* Summary */}
      {expanded && item.summary && (
        <div style={{
          marginTop: 10, paddingLeft: 17,
          fontSize: 12, color: C.muted, lineHeight: 1.7,
          borderLeft: `2px solid ${C.border}`,
        }}>
          {stripHtml(item.summary)}
        </div>
      )}
    </div>
  );
}

export default function NewsPage() {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [importance, setImportance] = useState<string | undefined>(undefined);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const offsetRef = useRef(0);
  const LIMIT = 50;

  const buildUrl = useCallback((off: number) => {
    const params = new URLSearchParams();
    if (category)   params.set("category", category);
    if (importance) params.set("importance", importance);
    if (search)     params.set("search", search);
    params.set("limit", String(LIMIT));
    params.set("offset", String(off));
    return `/api/v2/news?${params}`;
  }, [category, importance, search]);

  const load = useCallback(async () => {
    setLoading(true);
    setHasMore(true);
    offsetRef.current = 0;
    try {
      const res = await fetch(buildUrl(0));
      if (res.ok) {
        const data: NewsItem[] = await res.json();
        setItems(data);
        setHasMore(data.length === LIMIT);
        offsetRef.current = data.length;
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [buildUrl]);

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const res = await fetch(buildUrl(offsetRef.current));
      if (res.ok) {
        const data: NewsItem[] = await res.json();
        setItems((prev) => [...prev, ...data]);
        setHasMore(data.length === LIMIT);
        offsetRef.current += data.length;
      }
    } catch { /* ignore */ }
    setLoadingMore(false);
  }, [buildUrl, loadingMore, hasMore]);

  // Reload on filter change
  useEffect(() => { load(); }, [load]);

  // Auto-refresh every 60s
  useEffect(() => {
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, [load]);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setSearch(searchInput.trim());
  }

  const tabBtn = (active: boolean) => ({
    padding: '6px 14px', fontSize: 12, fontWeight: 600, fontFamily: 'monospace',
    background: 'transparent', border: 'none', cursor: 'pointer',
    borderBottom: `2px solid ${active ? C.accent : 'transparent'}`,
    color: active ? C.accent : C.muted,
  });

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '24px 16px', display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: C.text, fontFamily: 'monospace', margin: 0 }}>
            News Feed
          </h1>
          <p style={{ fontSize: 12, color: C.muted, fontFamily: 'monospace', marginTop: 4 }}>
            {items.length > 0 ? `${items.length} articles · ` : ''}auto-refresh 60s
          </p>
        </div>

        {/* Search */}
        <form onSubmit={handleSearch} style={{ display: 'flex', gap: 6 }}>
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search headlines..."
            style={{
              background: C.card, border: `1px solid ${C.border}`,
              borderRadius: 6, padding: '6px 12px', fontSize: 12,
              color: C.text, fontFamily: 'monospace', width: 220,
              outline: 'none',
            }}
          />
          <button type="submit" style={{
            background: C.accent, border: 'none', borderRadius: 6,
            padding: '6px 14px', fontSize: 12, fontWeight: 600,
            color: '#fff', cursor: 'pointer', fontFamily: 'monospace',
          }}>
            Search
          </button>
          {search && (
            <button type="button" onClick={() => { setSearch(""); setSearchInput(""); }} style={{
              background: C.border, border: 'none', borderRadius: 6,
              padding: '6px 10px', fontSize: 12, color: C.muted,
              cursor: 'pointer', fontFamily: 'monospace',
            }}>✕</button>
          )}
        </form>
      </div>

      {/* Category tabs */}
      <div style={{ display: 'flex', gap: 0, borderBottom: `1px solid ${C.border}` }}>
        {CATEGORY_TABS.map((t) => (
          <button key={t.label} onClick={() => setCategory(t.value)} style={tabBtn(category === t.value)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Importance filter */}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <span style={{ fontSize: 11, color: C.muted, fontFamily: 'monospace', marginRight: 4 }}>Importance:</span>
        {IMPORTANCE_TABS.map((t) => (
          <button
            key={t.label}
            onClick={() => setImportance(t.value)}
            style={{
              padding: '3px 10px', fontSize: 11, fontWeight: 600, fontFamily: 'monospace',
              border: `1px solid ${importance === t.value ? C.accent : C.border}`,
              borderRadius: 4, cursor: 'pointer',
              background: importance === t.value ? `${C.accent}20` : 'transparent',
              color: importance === t.value ? C.accent : C.muted,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* News list */}
      <div style={{ borderRadius: 8, border: `1px solid ${C.border}`, background: C.card, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 48, textAlign: 'center', color: C.muted, fontSize: 13, fontFamily: 'monospace' }}>
            Loading...
          </div>
        ) : items.length === 0 ? (
          <div style={{ padding: 48, textAlign: 'center', color: C.muted, fontSize: 13, fontFamily: 'monospace' }}>
            No articles found
          </div>
        ) : (
          <>
            {items.map((item) => (
              <NewsCard key={item.id} item={item} />
            ))}

            {/* Load more */}
            {hasMore && (
              <div style={{ padding: '16px', textAlign: 'center' }}>
                <button
                  onClick={loadMore}
                  disabled={loadingMore}
                  style={{
                    fontSize: 12, fontFamily: 'monospace', fontWeight: 600,
                    color: C.accent, background: 'transparent', border: `1px solid ${C.accent}40`,
                    borderRadius: 6, padding: '8px 24px', cursor: loadingMore ? 'default' : 'pointer',
                    opacity: loadingMore ? 0.5 : 1,
                  }}
                >
                  {loadingMore ? 'Loading...' : 'Load more'}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
