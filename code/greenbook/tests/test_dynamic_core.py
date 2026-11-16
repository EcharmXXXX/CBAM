"""Hand-accounting and behaviour tests for the dynamic core; synthetic inputs."""
from copy import deepcopy
import csv
from pathlib import Path
import unittest
from greenbook.make_instance import write_instance
from greenbook.make_dynamic_instance import write_dynamic_instance
from greenbook.data_model import Case
from greenbook.model.solve import solve_case
from greenbook.model.build_dynamic import build, InvestmentOption, terminal_residual
from greenbook.model.build_recipes import build as build_recipes
from greenbook.model.build_certificate import build as build_certificate
from greenbook.analysis.compare import write_tables
from greenbook.analysis.policy_search import search

ROOT = Path(__file__).resolve().parents[2]/"review_artifacts"/"dynamic_test_run"


class DynamicCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instance=write_instance(ROOT/"instance")
        cls.base=Case.load(cls.instance)

    def test_multiple_investment_years_are_distinct_batches(self):
        case=deepcopy(self.base)
        row=deepcopy(case.investments[("INV_EU_DRI",2027)])
        row["year"]="2028"
        case.investments[("INV_EU_DRI",2028)]=row
        pools,options=build(case)
        self.assertIn(("INV_EU_DRI",2027),options)
        self.assertIn(("INV_EU_DRI",2028),options)
        sol=solve_case(case,"C")
        self.assertEqual(sol.status,"optimal")
        self.assertIn(("INV_EU_DRI",2028),sol.inv)

    def test_loader_keeps_years_and_rejects_duplicate_investment_batches(self):
        folder=write_instance(ROOT/"loader_batches")
        path=folder/"10_investment_options.csv"
        with path.open(encoding="utf-8-sig",newline="") as file:
            reader=csv.DictReader(file); fields=reader.fieldnames; rows=list(reader)
        extra=dict(rows[0],year=2028)
        rows.append(extra)
        def save():
            with path.open("w",encoding="utf-8-sig",newline="") as file:
                writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        save()
        self.assertEqual(len(Case.load(folder).investments),4)
        rows.append(extra);save()
        with self.assertRaisesRegex(ValueError,"Duplicate investment batch"):Case.load(folder)

    def test_loader_requires_explicit_demand_case_selection(self):
        folder=write_instance(ROOT/"loader_demand")
        path=folder/"07_demand.csv"
        with path.open(encoding="utf-8-sig",newline="") as file:
            reader=csv.DictReader(file);fields=reader.fieldnames;rows=list(reader)
        original=rows[0]["demand_case"]
        rows += [dict(row,demand_case="OTHER",demand_tcs=float(row["demand_tcs"])*2) for row in list(rows)]
        with path.open("w",encoding="utf-8-sig",newline="") as file:
            writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        with self.assertRaisesRegex(ValueError,"Select one demand_case"):Case.load(folder)
        self.assertEqual(sum(Case.load(folder,demand_case="OTHER").demand.values()),2*sum(Case.load(folder,demand_case=original).demand.values()))

    def test_absolute_rights_with_no_recipe_mapping_are_rejected(self):
        case=deepcopy(self.base)
        case.attribute_rights=[dict(plant_id="UNKNOWN",year=2027,available_rights_tcs="1e6")]
        with self.assertRaisesRegex(ValueError,"verified recipe_id"):solve_case(case,"G1")

    def test_negative_mrv_fee_cannot_incentivise_unused_certificates(self):
        case=deepcopy(self.base)
        case.certificate_rules[("MAIN",2027)]["mrv_eur_per_certificate"]="-1"
        with self.assertRaisesRegex(ValueError,"MRV fees"):solve_case(case,"G1")

    def test_heterogeneous_asset_retirement_scope_and_utilization(self):
        case=deepcopy(self.base)
        a=deepcopy(case.assets[0]); b=deepcopy(a)
        a.update(pool_id="TEST",capacity_tpy="100",scope_share="0.5",max_utilization="0.8",retirement_year="2028")
        b.update(pool_id="TEST",capacity_tpy="100",scope_share="1",max_utilization="0.2",retirement_year="2029")
        case.assets=[a,b]
        pools,_=build(case)
        self.assertEqual(pools["TEST"].available(2027),60.)
        self.assertEqual(pools["TEST"].available(2028),20.)
        self.assertEqual(pools["TEST"].available(2029),0.)

    def test_committed_asset_only_available_from_commissioning(self):
        case=deepcopy(self.base)
        a=deepcopy(case.assets[0]); a.update(pool_id="PIPE",start_year="2028",status="committed",retirement_year="2040")
        case.assets=[a]
        pools,_=build(case)
        self.assertEqual(pools["PIPE"].available(2027),0.)
        self.assertGreater(pools["PIPE"].available(2028),0.)

    def test_future_project_residual_does_not_exceed_capex(self):
        x=InvestmentOption("F","TEST",100,0,10,20,2027,1e6,.9)
        self.assertEqual(terminal_residual(x,2027,2029),0.)
        x.lead_years=2
        self.assertEqual(terminal_residual(x,2027,2029),95.)
        self.assertEqual(terminal_residual(x,2027,2050),0.)

    def test_two_grades_require_additional_physical_steel(self):
        case=deepcopy(self.base)
        new="R_X_LONG"
        for year in case.years:
            case.recipes[(new,year)]=dict(case.recipes[("R_X_SCRAP",year)],recipe_id=new,grade_id="LONG",certificate_class="NONE")
            case.requirements[(new,year)]=deepcopy(case.requirements[("R_X_SCRAP",year)])
            case.emission_components[(new,year)]=deepcopy(case.emission_components[("R_X_SCRAP",year)])
            case.demand[("EU","LONG","MAIN",year)]=1e6
            case.demand[("X","LONG","MAIN",year)]=1e6
        # Match the existing case's demand scenario, without creating alternatives.
        old_case=next(iter(self.base.demand))[2]
        case.demand={(r,g,old_case,t):v for (r,g,c,t),v in case.demand.items()}
        case.steel_recipe_ids+= (new,)
        sol=solve_case(case,"N")
        self.assertEqual(sol.status,"optimal")
        for year in case.years:
            self.assertAlmostEqual(sum(v for (r,d,t),v in sol.x.items() if t==year),112.)
            self.assertAlmostEqual(sol.q[(new,year)],2.)

    def test_certificate_compatibility_is_year_specific(self):
        case=deepcopy(self.base)
        case.certificate_rules[("MAIN",2029)]["eu_use_rule"]="allow_all"
        cert=build_certificate(case,build_recipes(case))
        self.assertEqual(cert.attribute_use("R_X_H2DRI","EU",case,2027),0.)
        self.assertEqual(cert.attribute_use("R_X_H2DRI","EU",case,2029),1.)

    def test_annual_carbon_prices_and_cashflows_reconcile_to_pv(self):
        case=deepcopy(self.base)
        case.scenario_params[("COMMON","origin_carbon_price","X",2028)]=120.
        self.assertEqual(case.param("COMMON","origin_carbon_price","X",year=2027),20.)
        self.assertEqual(case.param("COMMON","origin_carbon_price","X",year=2028),120.)
        sol=solve_case(case,"C")
        self.assertEqual(sol.status,"optimal")
        pv=sum((1.07)**(-(t-2027))*sum(row.values()) for t,row in sol.annual_costs.items())
        self.assertAlmostEqual(pv,sol.objective_meur,places=5)
        for t in case.years:
            manual=sum(float(c["factor"])*float(c["ets_coverage_share"])*
                (120. if c["emission_country"]=="X" and t==2028 else 20. if c["emission_country"]=="X" else 80.)*sol.q[(rid,t)]
                for (rid,year),comps in case.emission_components.items() if year==t for c in comps)
            self.assertAlmostEqual(manual,sol.annual_costs[t]["origin_carbon_tax"],places=5)

    def test_shared_build_limits_and_absolute_attribute_rights(self):
        case=deepcopy(self.base)
        case.build_limits=[dict(limit_id="EU_DRI",node_id="EU",year="2027",limit_kind="new_build",process_or_resource="DRI",upper_value="0",linked_option_id="")]
        case.attribute_rights=[dict(recipe_id="R_X_H2DRI",plant_id="",year=str(t),available_rights_tcs="0") for t in case.years]
        sol=solve_case(case,"G1")
        self.assertEqual(sol.status,"optimal")
        self.assertAlmostEqual(sol.inv[("INV_EU_DRI",2027)],0.)
        for t in case.years:
            self.assertAlmostEqual(sol.z[("R_X_H2DRI",t)],0.)

    def test_unreplaced_precursor_duty_is_common_to_policy_scenarios(self):
        case=deepcopy(self.base)
        for (crossing,t),row in case.border_rules.items():
            if crossing=="HBI_X":
                row["replaced_scope"]="unreplaced"
        # Force HBI use to test cash accounting, not an assumed policy effect.
        for (rid,t),reqs in case.requirements.items():
            if rid=="R_EU_HBIEAF":
                for req in reqs:
                    if req["requirement_type"]=="opex":req["coefficient"]="-10000"
        for scenario in ("N","C","G1"):
            sol=solve_case(case,scenario)
            self.assertEqual(sol.status,"optimal")
            manual=sum(80.*1.3125*sol.y[("HBI_X",t)]/(1.07**(t-2027)) for t in case.years)
            self.assertAlmostEqual(sol.costs["unreplaced_border"],manual,places=5)
            self.assertGreater(manual,0.)

    def test_failed_case_exports_status_without_zero_emissions_claim(self):
        case=deepcopy(self.base)
        case.demand={k:1e12 for k in case.demand}
        sol=solve_case(case,"C")
        self.assertTrue(sol.status.startswith("infeasible"))
        folder=ROOT/"infeasible_output"
        write_tables(case,{"C":sol},folder)
        with (folder/"emissions_sys.csv").open(encoding="utf-8-sig") as f:
            self.assertEqual(len(list(csv.DictReader(f))),0)
        self.assertIn("infeasible",(folder/"summary.md").read_text(encoding="utf-8"))


class ExtendedCalendarTests(unittest.TestCase):
    def test_kappa_search_summary_matches_qualifying_grid_rows(self):
        instance=write_dynamic_instance(ROOT/"search_fixture")
        folder=ROOT/"search_result";folder.mkdir(exist_ok=True)
        result=search(Case.load(instance),[.25,.5,1.,4.],folder)
        with (folder/"kappa_grid.csv").open(encoding="utf-8-sig") as file:
            rows=list(csv.DictReader(file))
        qualifying=[row for row in rows if row["emissions_target_met"]=="True"]
        self.assertTrue(qualifying)
        self.assertEqual(result["smallest_examined_qualifying_kappa"],min(float(row["kappa"]) for row in qualifying))
        best=min(qualifying,key=lambda row:float(row["resource_cost_proxy_PV_Meur"]))
        self.assertEqual(result["lowest_cost_examined_qualifying_kappa"],float(best["kappa"]))

    def test_2026_2040_structural_case_has_all_years_and_three_year_lead(self):
        instance=write_dynamic_instance(ROOT/"dynamic_2026_2040")
        case=Case.load(instance)
        self.assertEqual(case.years,tuple(range(2026,2041)))
        self.assertEqual(len(case.investments),45)
        for scenario in ("N","C","G1","G05"):
            sol=solve_case(case,scenario)
            self.assertEqual(sol.status,"optimal")
            self.assertLess(sol.diagnostics["max_violation_Mt"],1e-6)
            pools,options=build(case)
            for t in (2026,2027,2028):
                self.assertAlmostEqual(sol.pool_usage[("EU_DRI",t)][1],0.)
            for (oid,vy),value in sol.inv.items():
                if vy+3>2040:self.assertAlmostEqual(value,0.)


if __name__=="__main__":unittest.main()
