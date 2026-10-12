// Small dependency-free SVG charts. Colours come from CSS variables so they follow the theme.

export function LineChart({ data }: { data: { label: string; value: number }[] }) {
  const W = 520, H = 160, P = 24;
  const max = Math.max(1, ...data.map((d) => d.value));
  const x = (i: number) => P + (i * (W - 2 * P)) / Math.max(1, data.length - 1);
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  const pts = data.map((d, i) => `${x(i)},${y(d.value)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Conversations per day">
      {[0, 0.5, 1].map((t) => <line key={t} x1={P} x2={W - P} y1={y(max * t)} y2={y(max * t)} className="grid" />)}
      <polyline points={pts} className="line" fill="none" />
      {data.map((d, i) => <circle key={i} cx={x(i)} cy={y(d.value)} r={3} className="dotp"><title>{`${d.label}: ${d.value}`}</title></circle>)}
      <text x={P} y={H - 6} className="axis">{data[0]?.label.slice(5)}</text>
      <text x={W - P} y={H - 6} className="axis" textAnchor="end">{data[data.length - 1]?.label.slice(5)}</text>
      <text x={P - 4} y={y(max) + 4} className="axis" textAnchor="end">{max}</text>
    </svg>
  );
}

export function BarList({ data, unit = "" }: { data: { label: string; value: number }[]; unit?: string }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  if (data.length === 0) return <p className="muted small">No data yet.</p>;
  return (
    <div className="barlist">
      {data.map((d) => (
        <div className="bar-row" key={d.label}>
          <span className="bar-label" title={d.label}>{d.label}</span>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${(d.value / max) * 100}%` }} /></div>
          <b>{Number.isInteger(d.value) ? d.value : d.value.toFixed(1)}{unit}</b>
        </div>
      ))}
    </div>
  );
}

export function Donut({ parts }: { parts: { label: string; value: number; cls: string }[] }) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  const R = 52, C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 140 140" className="donut" role="img" aria-label="Conversation outcomes">
        <circle cx="70" cy="70" r={R} className="donut-bg" fill="none" strokeWidth="16" />
        {total > 0 && parts.map((p) => {
          const len = (p.value / total) * C;
          const el = <circle key={p.label} cx="70" cy="70" r={R} fill="none" strokeWidth="16" className={p.cls}
            strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-offset} transform="rotate(-90 70 70)"><title>{`${p.label}: ${p.value}`}</title></circle>;
          offset += len;
          return el;
        })}
        <text x="70" y="68" textAnchor="middle" className="donut-num">{total}</text>
        <text x="70" y="86" textAnchor="middle" className="axis">total</text>
      </svg>
      <ul className="legend">{parts.map((p) => <li key={p.label}><i className={`swatch ${p.cls}`} />{p.label} <b>{p.value}</b></li>)}</ul>
    </div>
  );
}
