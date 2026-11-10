// Site settings for Structural Dynamics Watch.
export default {
  title: "Structural Dynamics Watch",
  root: "src",
  theme: "air", // light, minimal
  pages: [
    {name: "Home", path: "/"},
    {name: "Papers", path: "/papers"},
    {name: "PDB statistics", path: "/pdb"},
    {name: "News", path: "/news"},
    {name: "About", path: "/about"}
  ],
  head: '<meta name="robots" content="index,follow">',
  // A "Home" link at the top of every page, in addition to the sidebar.
  header: '<a href="/structural-dynamics-watch/" style="font-weight:600;text-decoration:none;">← Home</a>',
  footer: "Structural Dynamics Watch · updated nightly · data from PubMed, bioRxiv and the RCSB PDB",
  search: true
};
