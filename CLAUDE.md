## Progetto

Gestionale della sagra (Django + PostgreSQL in Docker): app `magazzino`, `cassa`, `bar`, `assistente` (server MCP),
`sistema` (pannello `/sistema/` con schema animato, errori, dispositivi, copia remota verso il server di casa).
Manuale tecnico: `docs/MANUTENZIONE.md` (si legge anche in fondo a `/sistema/`); guida per chi lo usa:
`magazzino/templates/magazzino/guida.html` (`/guida/`). Quando cambia qualcosa, aggiornare tutti e due.

- L'installazione vera (progetto Docker `magazzino-sagra`) gira da questa cartella: per provare non lanciare
  `docker compose up` qui, usa il server di prova (`docker compose -f docker-compose.yml -f docker-compose.debug.yml`, porta 8002).
- Le migrazioni già applicate non si modificano: se ne scrive una nuova.
- La logica sta nei `services.py`: viste e strumenti MCP li chiamano. Test: `python manage.py test` (anche su PostgreSQL nel server di prova).
- Tabelle che riguardano solo un server (battiti, sessioni, `sistema_*`) vanno in `TABELLE_LOCALI` (`sistema/copia.py`), senza chiavi esterne.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
