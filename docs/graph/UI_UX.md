# UI/UX — Graph Explorer

Integrated into vanilla SPA (not React): panel `#panel-graph`, nav «Граф связей».

Layout: filters | Cytoscape canvas | details + relations table.  
Deep link: `/?tab=graph&doc=<uuid>&depth=1&gmode=local`.  
Theme: existing dark CSS tokens; edge types differ by color + dash + arrow (not color alone).  
Truncation banner when `truncated=true`. Destroy Cytoscape on tab leave.
