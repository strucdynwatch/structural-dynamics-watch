---
title: Papers
toc: false
---

# Papers

```js
const data = FileAttachment("data/papers.json").json();
```

```js
const papers = data.papers;
const today = new Date(data.generated);
const daysAgo = (d) => (today - new Date(d)) / 864e5;
const years = [...new Set(papers.map((p) => (p.date || "").slice(0, 4)).filter(Boolean))].sort().reverse();
const facilityList = [...new Set(papers.flatMap((p) => p.facilities))].sort();
```

<div class="grid grid-cols-4">
  <div class="card"><h2>Papers since 2009</h2><span class="big">${papers.length.toLocaleString("en-US")}</span></div>
  <div class="card"><h2>Last 7 days</h2><span class="big">${papers.filter((p) => daysAgo(p.date) <= 7).length}</span></div>
  <div class="card"><h2>Last 30 days</h2><span class="big">${papers.filter((p) => daysAgo(p.date) <= 30).length}</span></div>
  <div class="card"><h2>Preprints not yet published</h2><span class="big">${papers.filter((p) => p.type === "preprint").length}</span></div>
</div>

```js
// Filters chosen by clicking an institution or facility on a paper.
const picked = Mutable({institution: null, facility: null});
const pick = (key, value) => (picked.value = {...picked.value, [key]: value});
const shown = Mutable(50);
const showMore = () => (shown.value += 50);
const resetShown = () => (shown.value = 50);
```

```js
const query = view(Inputs.text({placeholder: "Search title, authors, abstract, journal…", width: 420}));
const period = view(Inputs.select(["All years", "Last 7 days", "Last 30 days", ...years], {label: "Period"}));
const kind = view(Inputs.radio(["All", "Published", "Preprints only"], {label: "Type", value: "All"}));
const facilityPick = view(Inputs.select(["Any facility", ...facilityList], {label: "Facility"}));
```

```js
resetShown(); // back to the first 50 whenever a filter changes
const words = query.toLowerCase().split(/\s+/).filter(Boolean);
const facility = picked.facility ?? (facilityPick === "Any facility" ? null : facilityPick);
const matches = papers.filter((p) => {
  if (period === "Last 7 days" && daysAgo(p.date) > 7) return false;
  if (period === "Last 30 days" && daysAgo(p.date) > 30) return false;
  if (/^\d{4}$/.test(period) && !(p.date || "").startsWith(period)) return false;
  if (kind === "Published" && p.type !== "article") return false;
  if (kind === "Preprints only" && p.type !== "preprint") return false;
  if (facility && !p.facilities.includes(facility)) return false;
  if (picked.institution && p.institution !== picked.institution) return false;
  if (words.length) {
    const hay = `${p.title} ${p.authors.join(" ")} ${p.abstract} ${p.journal} ${p.institution}`.toLowerCase();
    if (!words.every((w) => hay.includes(w))) return false;
  }
  return true;
});
```

```js
const chips = [
  picked.institution && html`<button class="chip" onclick=${() => pick("institution", null)}>Institution: ${picked.institution} ×</button>`,
  picked.facility && html`<button class="chip" onclick=${() => pick("facility", null)}>Facility: ${picked.facility} ×</button>`
].filter(Boolean);
display(html`<p class="count">${matches.length.toLocaleString("en-US")} papers ${chips}</p>`);
```

```js
function authorLine(a) {
  if (a.length <= 6) return a.join(", ");
  return `${a.slice(0, 3).join(", ")}, … ${a.at(-1)}`;
}
function link(href, text) {
  return html`<a href=${href} target="_blank" rel="noopener">${text}</a>`;
}
function paper(p) {
  const doiUrl = p.doi ? `https://doi.org/${p.doi}` : null;
  const status = p.type === "article"
    ? html`<span class="badge pub">Published</span>`
    : html`<span class="badge pre">Preprint</span>`;
  const links = [
    p.doi && link(doiUrl, p.type === "article" ? "Published version (DOI)" : "Preprint (DOI)"),
    p.pmid && link(`https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/`, "PubMed"),
    p.preprint && html`<span class="also"><span class="badge pre">Preprint</span> ${link(`https://doi.org/${p.preprint.doi}`, `${p.preprint.server}, ${p.preprint.date}`)}</span>`,
    p.published_doi && html`<span class="also"><span class="badge pub">Published</span> ${link(`https://doi.org/${p.published_doi}`, "journal version")}</span>`
  ].filter(Boolean);
  const extra = [
    p.institution && html`<a href="#" class="quiet" title="Show only papers from this institution" onclick=${(e) => { e.preventDefault(); pick("institution", p.institution); }}>${p.institution}</a>`,
    ...p.facilities.map((f) => html`<a href="#" class="quiet" title="Show only papers mentioning this facility" onclick=${(e) => { e.preventDefault(); pick("facility", f); }}>${f}</a>`)
  ].filter(Boolean);
  return html`<article class="paper">
    <div class="meta">${p.date} · ${status} ${p.journal}</div>
    <h3>${doiUrl ? link(doiUrl, p.title) : p.title}</h3>
    <div class="authors" title=${p.authors.join(", ")}>${authorLine(p.authors)}</div>
    ${extra.length ? html`<div class="extra">${extra.flatMap((x, i) => (i ? [" · ", x] : [x]))}</div>` : ""}
    <div class="links">${links.flatMap((x, i) => (i ? [" · ", x] : [x]))}</div>
    ${p.abstract ? html`<details><summary>Abstract</summary><p>${p.abstract}</p></details>` : ""}
  </article>`;
}
display(html`<div>${matches.slice(0, shown).map(paper)}</div>`);
if (matches.length > shown) display(html`<button class="more" onclick=${showMore}>Show 50 more</button>`);
```

<p class="muted small">Updated ${data.generated.slice(0, 10)}. Sources: PubMed and bioRxiv (via Europe PMC). Searches titles and abstracts; facilities are detected from the abstract and may be incomplete.</p>

<style>
.big { font-size: 2rem; font-weight: 600; }
.count { color: var(--theme-foreground-muted); }
.paper { border-bottom: 1px solid var(--theme-foreground-faintest); padding: 0.9rem 0; max-width: 820px; }
.paper h3 { margin: 0.2rem 0; font-size: 1.05rem; line-height: 1.35; }
.paper h3 a { color: inherit; text-decoration: none; }
.paper h3 a:hover { text-decoration: underline; }
.meta, .authors, .links, .extra { font-size: 0.85rem; }
.meta, .authors { color: var(--theme-foreground-muted); }
.extra, .extra a.quiet { color: var(--theme-foreground-faint); font-size: 0.8rem; text-decoration: none; }
.extra a.quiet:hover { text-decoration: underline; color: var(--theme-foreground-focus); }
.badge { font-size: 0.7rem; font-weight: 600; padding: 0.05rem 0.4rem; border-radius: 0.6rem; text-transform: uppercase; letter-spacing: 0.03em; }
.badge.pub { background: #e5f4e4; color: #2f6b2c; }
.badge.pre { background: #f9eeda; color: #7a5410; }
details summary { cursor: pointer; font-size: 0.85rem; color: var(--theme-foreground-focus); margin-top: 0.3rem; }
details p { font-size: 0.9rem; line-height: 1.5; }
.chip { margin-left: 0.5rem; font-size: 0.8rem; border: 1px solid var(--theme-foreground-faint); border-radius: 1rem; background: none; padding: 0.1rem 0.6rem; cursor: pointer; }
.more { margin: 1rem 0; padding: 0.4rem 1rem; cursor: pointer; }
.small { font-size: 0.8rem; }
</style>
