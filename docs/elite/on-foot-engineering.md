# On-foot (Odyssey) engineering

> *"I know Odyssey on-foot engineering — suit and weapon grade upgrades and their materials,
> the modification perks, and which of the 13 on-foot engineers unlocks each, where they are
> and how to reach them."*

The on-foot counterpart to the ship [engineers finder](engineers.md) and
[blueprint & material sourcing](blueprints.md). It answers suit, weapon, modification and
on-foot-engineer questions from a **bundled offline reference** — never a vague guess.

**Example:** *"how do I engineer my Maverick suit"*

## What you can ask

| You say… | It tells you… |
|----------|---------------|
| *"How do I engineer my Maverick suit?"* | The grade 1→5 upgrade recipe (materials per grade + where to source them) and the suit modifications you can add |
| *"What do I need to upgrade my Dominator to grade 5?"* | The exact grade-5 material list, applied at a Pioneer Supplies vendor |
| *"Engineer my Karma AR-50"* / *"upgrade my Manticore Oppressor"* | The weapon's family, damage type, and its grade-upgrade materials |
| *"Which engineer gives Greater Range?"* | Every on-foot engineer who offers that modification, each tagged with **your** unlock status |
| *"Where is Domino Green?"* / *"how do I unlock Hero Ferrari?"* | Their system and workshop, how to access + unlock them, who they refer you to, the mods they offer — and it copies their system to your clipboard to plot a route |
| *"Give me the full on-foot engineering breakdown"* | A short overview of both halves of the system |

## The two halves of on-foot engineering

1. **Grade upgrades (1→5)** raise a suit's or weapon's base stats. They're applied at any
   concourse with a **Pioneer Supplies** vendor — *not* engineer-gated — and cost materials.
   The three engineerable suits are the **Maverick**, **Dominator** and **Artemis** (the basic
   Flight Suit can't be engineered); the weapons are the **Karma** (kinetic), **TK** (laser)
   and **Manticore** (plasma) families.
2. **Modifications** are perks — Greater Range, Extra Backpack Capacity, Night Vision, Magazine
   Size and so on — applied by a specific **on-foot engineer** once you've unlocked them.

## The 13 on-foot engineers

Nine are in the bubble (Domino Green, Kit Fowler, Yarden Bond, Hero Ferrari, Wellington Beck,
Uma Laszlo, Jude Navarro, Terra Velasquez, Oden Geiger) and four in Colonia (Baltanos, Eleanor
Bresa, Rosa Dayette, Yi Shen). Several unlock via **referral chains** — e.g. Domino Green →
Kit Fowler → Yarden Bond — which COVAS++ names when you ask about an engineer.

## Journal-grounded status

On-foot engineers share the journal's `EngineerProgress` event with ship engineers, so when you
ask about one — or about who offers a modification — COVAS++ tags them with **your** live status
(not yet started / discovered / invited / unlocked). If it hasn't seen an `EngineerProgress`
event yet this session, it gives the requirement from the table instead of guessing.

## The reference table

Locations, unlock tasks, the modification catalogue and the grade-upgrade recipes come from a
**bundled offline table** (`covas/ed/odyssey_engineering.py`) — no network at runtime. Frontier
occasionally tweaks requirements or relocates engineers between updates; the table documents its
community sources (Inara, the Elite Dangerous wiki) and its refresh date. Your **status** is
always live from the journal.

!!! note "Live material stock is a follow-up"
    Recipes report what a grade upgrade **needs**. Cross-referencing your live suit/weapon
    material stock (ShipLocker / BackPack) to compute what you're **short** on — the way the
    ship [blueprint capability](blueprints.md) does with ship materials — is a planned
    follow-up (there's no ShipLocker/BackPack parsing yet).

## Settings

This reads **only local journal data** and a bundled table — no game-account login or private
API. It needs [game-state monitoring](monitoring.md) (`[elite].enabled = true`) for the live
unlock status; the reference data itself works regardless.
