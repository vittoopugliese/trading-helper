# Biblioteca técnica — índice de notas de libros

Notas destiladas de los libros de `C:\Users\torib\Desktop\Imagenes\Trading\Libros`,
convertidas en conocimiento accionable que alimenta el análisis (RAG en
`agent/knowledge.py` → se inyecta en el prompt de `run_deep_analysis`).

> Este archivo empieza con `_` a propósito: el indexador lo ignora (es índice para
> vos, no chunk para el buscador). El resto de `notes/libros/**` sí se indexa.

## Fuentes

| Libro | Autor | Notas derivadas |
|-------|-------|-----------------|
| Librocompleto Elliott Ondas / Teoría de ondas de Elliott / Ondas Zig-Zag Planas | varios | `elliott-01..05` |
| Método Wyckoff de Trading | Enrique Díaz Valdecantos | `wyckoff-01..05` |
| El Trader Disciplinado | Mark Douglas | `psico-douglas-01..02` |
| El juego mental del trading (resumen) | Jared Tendler | `psico-tendler-01..02` |
| Vivir del Trading | Alexander Elder | `elder-01..03`, `psico-elder-01` |
| Tesina: Fractales en los mercados financieros | — | `fractales-01` |
| Trading Plan (BEIG Capital / Lukaku) | Black Eagles Capital | `plan-beig-01` |
| 21 cualidades indispensables de un líder | J. Maxwell | `psico-liderazgo-01` (disciplina) |

## Cómo se usa en el helper

`analyze()` construye una query semántica desde el snapshot de mercado + contexto
Elliott, busca las notas más relevantes (`search_notes`) y las pasa a Claude en el
análisis profundo. Cuanto más rica y bien seccionada la base, mejores los análisis.
La nota `sintesis-confluencias.md` ata estos conceptos al checklist A+ del motor.
