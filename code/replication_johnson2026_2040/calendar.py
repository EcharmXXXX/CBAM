"""Calendar years, construction lead time and initial project pipeline."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCalendar:
    start_year: int = 2026
    end_year: int = 2040
    construction_years: int = 3

    def __post_init__(self):
        if not all(type(x) is int for x in (self.start_year,self.end_year,self.construction_years)):
            raise ValueError("Years and construction lead time must be integers")
        if self.end_year < self.start_year or self.construction_years < 0:
            raise ValueError("Invalid calendar or construction lead time")

    @property
    def production_years(self):
        return tuple(range(self.start_year,self.end_year+1))

    @property
    def parameter_years(self):
        # A decision in the final production year still looks ahead by the
        # construction lead time. These extra years are not observed outcomes.
        return tuple(range(self.start_year,self.end_year+self.construction_years+1))

    @property
    def initial_pipeline_years(self):
        return tuple(range(self.start_year,self.start_year+self.construction_years))

    def commissioning_year(self,decision_year):
        if decision_year not in self.production_years:
            raise ValueError("Decision year is outside the simulation horizon")
        return decision_year+self.construction_years

    def annual_index(self,calendar_year):
        if calendar_year not in self.parameter_years:
            raise ValueError("No parameter slot for this calendar year")
        return calendar_year-self.start_year
