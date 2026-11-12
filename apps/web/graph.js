/** Bootstrap Obsidian-style graph tab (GraphUI from js/graph/graph-app.js). */
document.addEventListener("DOMContentLoaded", () => {
  if (window.GraphUI && typeof window.GraphUI.wire === "function") {
    window.GraphUI.wire();
  }
});
