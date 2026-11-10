---
title: Review queue
toc: false
index: false
---

# Review queue

```js
const data = FileAttachment("data/papers.json").json();
```

```js
const queue = data.papers.filter((p) => p.status === "review");
const editUrl = "https://github.com/strucdynwatch/structural-dynamics-watch/edit/main/curation/include.txt";
```

Search hits that are **not shown** on the Papers page because they look like chemistry, materials, physics or instrumentation rather than biosciences. If one belongs on the site, copy its line and add it to [include.txt](${editUrl}).

```js
const q = view(Inputs.text({placeholder: "Search the review queue…", width: 420}));
```

```js
const words = q.toLowerCase().split(/\s+/).filter(Boolean);
const items = queue.filter((p) => {
  if (!words.length) return true;
  const hay = `${p.title} ${p.abstract} ${p.journal} ${p.authors.join(" ")}`.toLowerCase();
  return words.every((w) => hay.includes(w));
});
const lineFor = (p) => `${p.pmid || p.doi}  # ${p.title.slice(0, 60)}`;
function copyButton(p) {
  const b = html`<button class="copy">Copy line</button>`;
  b.onclick = async () => {
    try { await navigator.clipboard.writeText(lineFor(p)); b.textContent = "Copied"; }
    catch { b.textContent = lineFor(p); }
  };
  return b;
}
display(html`<p class="count">${items.length.toLocaleString("en-US")} of ${queue.length.toLocaleString("en-US")} held for review</p>`);
display(html`<div>${items.slice(0, 300).map((p) => html`<article class="paper">
  <div class="meta">${p.date} · ${p.journal} · ${p.pmid ? `PMID ${p.pmid}` : p.doi}</div>
  <h3>${p.doi ? html`<a href=${`https://doi.org/${p.doi}`} target="_blank" rel="noopener">${p.title}</a>` : p.title}</h3>
  <div class="why">Bioscience terms: ${p.bio_terms.join(", ") || "none"} · Other terms: ${p.other_terms.join(", ") || "none"}</div>
  ${p.abstract ? html`<details><summary>Abstract</summary><p>${p.abstract}</p></details>` : ""}
  ${copyButton(p)}
</article>`)}</div>`);
if (items.length > 300) display(html`<p class="count">Showing the newest 300; use the search box to find others.</p>`);
```

<style>
.count { color: var(--theme-foreground-muted); }
.paper { border-bottom: 1px solid var(--theme-foreground-faintest); padding: 0.8rem 0; max-width: 820px; }
.paper h3 { margin: 0.2rem 0; font-size: 1rem; }
.paper h3 a { color: inherit; text-decoration: none; }
.meta, .why { font-size: 0.8rem; color: var(--theme-foreground-muted); }
details summary { cursor: pointer; font-size: 0.85rem; color: var(--theme-foreground-focus); margin-top: 0.3rem; }
details p { font-size: 0.9rem; line-height: 1.5; }
.copy { margin-top: 0.4rem; font-size: 0.8rem; cursor: pointer; }
</style>
