"""Unit tests for the per-ship engineering-planning capability (issue #135; offline, DESIGN §9).

Drives both tools through stubbed getters over a hand-built remembered loadout + the recorded
material fixture + a faked engineer-progress map — no journal directory, no network. Locks: the
remembered-build summary, the grounded material-shortfall plan, the engineer-status line, the
honest paths (no remembered build, stock module not guessed, unknown module), and a plan ->
checklist round-trip through the REAL ChecklistCapability CRUD (the LLM-native bridge).
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.checklist_capability import ChecklistCapability
from covas.capabilities.ship_engineering_plan_capability import ShipEngineeringPlanCapability
from covas.checklist import Checklist
from covas.ed.engineers import EngineerStatus
from covas.ed.loadout import Engineering, LoadoutSnapshot, ShipModule
from covas.ed.materials import parse_materials

_MATERIALS = Path(__file__).parent / "fixtures" / "journal_materials.json"


def _materials():
    return parse_materials(json.loads(_MATERIALS.read_text(encoding="utf-8")))


def _snap(ship_id: int = 46) -> LoadoutSnapshot:
    """A Python with an engineered (G5) FSD, an engineered (G3) thruster, and a stock distributor."""
    return LoadoutSnapshot(
        ship="python", ship_id=ship_id, ship_name="Void Runner", ship_ident="VR-01",
        modules=(
            ShipModule(slot="FrameShiftDrive", item="int_hyperdrive_size5_class5",
                       engineering=Engineering(blueprint="FSD_LongRange", level=5,
                                               engineer="Farseer")),
            ShipModule(slot="MainEngines", item="int_engine_size5_class5",
                       engineering=Engineering(blueprint="Engine_Dirty", level=3)),
            ShipModule(slot="PowerDistributor", item="int_powerdistributor_size4_class5"),
        ),
    )


def _cap(*, snap=None, owned=None, materials="fixture", progress=None):
    remembered = _snap() if snap is None else snap
    fleet = owned if owned is not None else [
        {"ship_id": "46", "ship_type": "python", "name": "Void Runner", "active": True}]
    mats = _materials() if materials == "fixture" else materials
    return ShipEngineeringPlanCapability(
        get_owned=lambda: fleet,
        get_ship_loadout=lambda sid: remembered if str(sid) == "46" else None,
        get_active_loadout=lambda: remembered,
        get_materials=lambda: mats,
        get_progress=lambda: (progress or {}),
    )


# --- remembered build summary ----------------------------------------------------------------

def test_status_summarizes_the_remembered_build():
    out = _cap().run_tool("remembered_ship_build", {})
    assert 'Void Runner' in out
    assert "Increased Range grade 5" in out          # engineered FSD, by real blueprint name
    assert "Dirty Drive Tuning grade 3" in out       # engineered thruster
    assert "power distributor" in out.lower()        # the stock core module, flagged as still stock


def test_status_for_a_ship_with_no_remembered_build_is_honest():
    out = _cap().run_tool("remembered_ship_build", {"ship": "anaconda"})
    # 'anaconda' isn't in the fleet -> honest, lists what IS owned, never invents a build.
    assert "anaconda" in out.lower()
    assert "Void Runner" in out


def test_status_when_ship_owned_but_build_not_yet_captured():
    fleet = [{"ship_id": "77", "ship_type": "anaconda", "name": "The Ark", "active": True}]
    cap = _cap(owned=fleet, snap=_snap(77))          # get_ship_loadout only knows id 46
    out = cap.run_tool("remembered_ship_build", {"ship": "anaconda"})
    assert "don't have a remembered build" in out.lower()
    assert "board it" in out.lower()


# --- grounded material-shortfall plan --------------------------------------------------------

def test_plan_reports_current_grade_shortfall_and_engineer():
    out = _cap().run_tool("plan_engineering_upgrade",
                          {"module": "thrusters", "target_grade": 5})
    assert "Dirty Drive Tuning (grade 3)" in out             # current, from the remembered build
    assert "To grade 5" in out
    assert "Pharmaceutical Isolators" in out                 # a real G5 Dirty recipe material
    assert "SHORT on" in out                                 # computed against the live inventory
    assert "Felicity Farseer" in out                        # engineer who applies thruster mods
    assert "checklist" in out.lower()                        # invites the checklist hand-off


def test_plan_defaults_to_grade_5():
    out = _cap().run_tool("plan_engineering_upgrade", {"module": "thrusters"})
    assert "To grade 5" in out


def test_plan_engineer_status_reflects_live_progress():
    prog = {"Felicity Farseer": EngineerStatus(progress="Unlocked", rank=5)}
    out = _cap(progress=prog).run_tool("plan_engineering_upgrade",
                                       {"module": "thrusters", "target_grade": 5})
    assert "Felicity Farseer (unlocked, grade 5)" in out


def test_plan_when_already_at_target_says_so():
    out = _cap().run_tool("plan_engineering_upgrade", {"module": "FSD", "target_grade": 5})
    assert "already at grade 5" in out
    assert "SHORT" not in out                                 # no shortfall math when done


def test_plan_stock_module_asks_which_blueprint_never_guesses():
    out = _cap().run_tool("plan_engineering_upgrade", {"module": "power distributor"})
    assert "still stock" in out.lower()
    assert "which blueprint" in out.lower()
    assert "Overcharged" in out                               # only REAL distributor blueprints
    assert "SHORT" not in out                                 # nothing computed for a guessed recipe


def test_plan_no_materials_yet_is_honest():
    out = _cap(materials=None).run_tool("plan_engineering_upgrade",
                                        {"module": "thrusters", "target_grade": 5})
    assert "To grade 5" in out                                # recipe still spoken
    assert "haven't read your materials" in out.lower()       # honest, no invented shortfall


def test_plan_unknown_module_lists_what_is_fitted():
    out = _cap().run_tool("plan_engineering_upgrade", {"module": "chaff launcher"})
    assert "don't see 'chaff launcher'" in out
    assert "Frame Shift Drive" in out                         # names a real fitted module


# --- ship resolution / fleet-empty paths -----------------------------------------------------

def test_no_owned_ships_is_honest():
    cap = _cap(owned=[], snap=_snap())
    out = cap.run_tool("remembered_ship_build", {"ship": "python"})
    assert "haven't recorded any" in out.lower()


# --- the checklist bridge: plan -> real checklist CRUD round-trip -----------------------------

def test_plan_to_checklist_roundtrip_via_real_crud(tmp_path: Path):
    """The capability RETURNS a grounded plan; the model records it via the EXISTING checklist
    add_objective tool. This exercises that bridge end-to-end over the real Checklist CRUD:
    derive the plan, add an engineering objective from it, find it, complete it."""
    plan = _cap().run_tool("plan_engineering_upgrade",
                           {"module": "thrusters", "target_grade": 5})
    assert "SHORT on" in plan

    checklist = Checklist(str(tmp_path / "ultimate_checklist.md"))
    cl_cap = ChecklistCapability(checklist)

    # A task the model would author from the plan (real names/counts from THIS plan's output).
    task = "Engineer thrusters to grade 5 (Dirty Drive Tuning) — see Felicity Farseer"
    added = cl_cap.run_tool("add_objective", {"text": task})
    assert "Added #1" in added and "grade 5" in added

    # Round-trips through the same CRUD: it's the next pending, findable, and completable.
    nxt = cl_cap.run_tool("get_next_objectives", {"count": 1})
    assert "Engineer thrusters to grade 5" in nxt

    found = cl_cap.run_tool("find_objectives", {"query": "thrusters"})
    assert "#1" in found and "pending" in found

    done = cl_cap.run_tool("set_objective", {"query": "thrusters", "completed": True})
    assert "completed" in done
    assert checklist.progress() == (1, 1)                     # 1 of 1 complete


# --- registry / help contract ----------------------------------------------------------------

def test_tools_and_help_are_well_formed():
    cap = _cap()
    reg = CapabilityRegistry()
    reg.register(cap)
    names = {t["name"] for t in cap.tools()}
    assert names == {"remembered_ship_build", "plan_engineering_upgrade"}
    assert not help_meta_problems(cap.help_meta())


def test_unknown_tool_is_soft():
    assert "Unknown tool" in _cap().run_tool("nope", {})
