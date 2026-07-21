/* =====================================================================
   DATOS DE DEMOSTRACION
   Este archivo lo REESCRIBE automaticamente el sistema con tus relojes
   reales cada vez que corre:   python -m src.main site
   Se genera aqui de forma procedural solo para que puedas ver la pagina
   funcionando antes de cargar tus 30 links.
   ===================================================================== */
(() => {
  // Generador pseudoaleatorio con semilla: la demo siempre se ve igual.
  let seed = 20260720;
  const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;

  const CATALOG = [
    ["B0C7KXX111", "Seiko 5 Sports SRPD55 Automatico 40mm",        215, "automatico", 185],
    ["B08QJ7RB22", "Casio G-Shock GA-2100-1A1 CasiOak",            108, "deportivo",   85],
    ["B07PGL3T33", "Orient Bambino V2 FAC00005W0",                 160, "vestir",     129],
    ["B09MTJ8Q44", "Citizen Eco-Drive BM8180-03E",                 135, "casual",     105],
    ["B00R9DFQ55", "Casio Vintage A158WA-1 Acero",                  27, "casual",      18],
    ["B07YTHM366", "Timex Weekender 40mm",                          48, "casual",      35],
    ["B08LNQK777", "Seiko Prospex SRPD21 Turtle",                  430, "buceo",      370],
    ["B07C4X9K88", "Casio Edifice EFV-100D",                        92, "deportivo",   72],
    ["B0BFPY7L99", "Tissot Everytime 40mm Suizo",                  295, "vestir",     245],
    ["B09VVN5T10", "Invicta Pro Diver 8926OB",                      95, "buceo",       62],
    ["B08F7CBM11", "Orient Kamasu RA-AA0004E",                     265, "buceo",      215],
    ["B07KLM2N12", "Bulova Marine Star 98B203",                    210, "deportivo",  165],
  ];

  const DAYS = 60;
  const today = new Date();
  const iso = d => d.toISOString().slice(0, 10);
  const median = a => { const s=[...a].sort((x,y)=>x-y), m=s.length>>1;
                        return s.length%2 ? s[m] : (s[m-1]+s[m])/2; };

  const items = CATALOG.map(([asin, name, base, category, target], idx) => {
    // Serie de precios: nivel base + deriva suave + escalones tipo promocion
    const history = [];
    let level = base * (0.98 + rnd() * 0.06);
    const drift = (rnd() - 0.55) * base * 0.0016;
    for (let k = DAYS - 1; k >= 0; k--) {
      const d = new Date(today); d.setDate(today.getDate() - k);
      level += drift + (rnd() - 0.5) * base * 0.012;
      let p = level;
      if (rnd() < 0.05) p = level * (0.86 + rnd() * 0.06);      // promocion puntual
      p = Math.max(base * 0.72, Math.min(base * 1.12, p));
      history.push([iso(d), Math.round(p * 100) / 100]);
    }
    // Un par de relojes aterrizan hoy en su minimo, para ver el estado "COMPRAR"
    if (idx % 5 === 0) {
      const lowest = Math.min(...history.map(h => h[1]));
      history[history.length - 1][1] = Math.round(lowest * 0.985 * 100) / 100;
    }

    const prices = history.map(h => h[1]);
    const price = prices[prices.length - 1];
    const prev = prices[prices.length - 2];
    const minOf = arr => { const m = Math.min(...arr.map(h => h[1]));
                           return [m, arr.find(h => h[1] === m)[0]]; };
    const [min7, min7d]   = minOf(history.slice(-7));
    const [min30, min30d] = minOf(history.slice(-30));
    const [minAll, minAllD] = minOf(history);
    const med = median(prices);
    const mean = prices.reduce((a, b) => a + b, 0) / prices.length;
    const vol = Math.sqrt(prices.reduce((s, p) => s + (p - mean) ** 2, 0) / prices.length) / mean * 100;
    const weekAgo = prices[prices.length - 8];

    const triggered = [];
    if (price <= min7 * 1.005)   triggered.push("min_7d");
    if (price <= min30 * 1.005)  triggered.push("min_30d");
    if (price <= minAll * 1.005) triggered.push("min_all_time");
    if (price <= target)         triggered.push("target_price");
    if ((price - prev) / prev * 100 <= -5) triggered.push("daily_drop");

    const W = {min_all_time:45, min_30d:25, min_7d:12, target_price:30, daily_drop:15};
    let score = triggered.reduce((s, r) => s + (W[r] || 0), 0);
    const vsMin = (price - minAll) / minAll * 100;
    if (vsMin <= 2) score += 15; else if (vsMin <= 8) score += 8;
    const disc = (med - price) / med * 100;
    if (disc >= 20) score += 15; else if (disc >= 10) score += 8;
    score = Math.max(0, Math.min(100, Math.round(score)));

    const slope = (prices.slice(-14).at(-1) - prices.slice(-14)[0]) / 14 * 7 / mean * 100;
    const dias = ["Lunes","Martes","Miercoles","Jueves","Viernes","Sabado","Domingo"];

    return {
      asin, name, category,
      url: "https://www.amazon.com/dp/" + asin,
      price, prev_price: prev, target_price: target,
      min_7d: min7, min_7d_date: min7d,
      min_30d: min30, min_30d_date: min30d,
      min_all: minAll, min_all_date: minAllD,
      max_all: Math.max(...prices),
      median_all: Math.round(med * 100) / 100,
      pct_vs_prev: (price - prev) / prev * 100,
      pct_vs_min_all: vsMin,
      discount_vs_typical: disc,
      weekly_change: (price - weekAgo) / weekAgo * 100,
      volatility_pct: vol,
      trend: slope <= -1.5 ? `bajando (${slope.toFixed(1)}%/sem)`
           : slope >= 1.5  ? `subiendo (+${slope.toFixed(1)}%/sem)` : "estable",
      best_weekday: dias[Math.floor(rnd() * 7)],
      history_days: DAYS,
      at_all_time_low: triggered.includes("min_all_time"),
      triggered,
      score,
      recommendation: score >= 60 ? "COMPRAR AHORA" : score >= 30 ? "MONITOREAR" : "ESPERAR",
      history,
    };
  });

  window.RADAR_DATA = {
    demo: true,
    generated_at: today.toLocaleString("es-CO", {dateStyle: "long", timeStyle: "short"}),
    currency_symbol: "$",
    days_tracked: DAYS,
    items,
  };
})();
