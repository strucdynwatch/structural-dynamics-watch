// Site settings for Structural Dynamics Watch.
export default {
  title: "Structural Dynamics Watch",
  root: "src",
  theme: "air", // light, minimal
  pages: [
    {name: "Papers", path: "/papers"},
    {name: "PDB statistics", path: "/pdb"},
    {name: "News", path: "/news"},
    {name: "About", path: "/about"}
  ],
  head: '<meta name="robots" content="index,follow">',
  footer: "Structural Dynamics Watch · updated nightly · data from PubMed, bioRxiv and the RCSB PDB",
  search: true
};
