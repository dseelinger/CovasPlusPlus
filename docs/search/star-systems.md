# Star systems

> *"I find the nearest star system matching an allegiance, government, economy, security,
> population, Powerplay, or colonization state, and copy its name to your clipboard."*

Find the nearest **star system** matching traits you describe. Say what matters; anything you don't
mention is left open.

**Example:** *"find the nearest Empire system with high security"*

> *When it's mid-search:* "Tell me the traits — allegiance, economy, security, and so on — and I'll
> find the nearest system that matches."

## The traits you can filter on

| Trait | Say something like… | What it restricts |
|-------|---------------------|-------------------|
| **Allegiance** | *"the closest Alliance system"* | A superpower — Federation, Empire, Alliance, Independent, Guardian, or Thargoid |
| **Government** | *"a nearby Democracy system"* | A government type — Democracy, Corporate, Anarchy, Theocracy… |
| **Economy** | *"the nearest High Tech system"* | A main economy — Extraction, High Tech, Agriculture, Industrial… |
| **Security** | *"a nearby high security system"* | Security level — High, Medium, Low, or Anarchy |
| **Powerplay power** | *"the closest system under Aisling Duval"* | A Powerplay power present in the system |
| **Powerplay state** | *"the nearest Stronghold system"* | Powerplay state — Stronghold, Fortified, Exploited, or Unoccupied |
| **Population** | *"a nearby system with over a billion people"* | A minimum or range of population |
| **Permit** | *"the nearest permit-locked system"* | Require, or exclude, permit-locked systems |
| **Colonised** | *"the closest uncolonised system"* | Already-colonised or uncolonised systems |
| **Open for colonization** | *"the nearest system open for colonization"* | Systems currently open for or undergoing colonization |

Combine as many as you like — *"the nearest Empire High Tech system with high security"* — and
refine turn by turn.

## Validated, not guessed

Spoken filter values are checked against a bundled canonical vocabulary (harvested from the live
Spansh API) before any query runs. A misheard filter is corrected or queried back, never silently
widened, and the result system is verified before it's spoken and copied.

## Settings

| Setting | What it does |
|---------|--------------|
| `star_systems.enabled` | Master switch for star-system search |
| `star_systems.search_size` | How many nearby matching systems to fetch; the closest is the answer |

See the [Configuration reference](../configuration.md#navigation-search-nav-star_systems-search).
