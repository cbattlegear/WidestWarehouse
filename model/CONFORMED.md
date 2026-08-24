# Conformed Objects Contract

These objects are owned by `model/common/` and are **referenced, never redefined**, by
subject-area model files. Referencing a name not on this list means you must define it
inside your own subject-area file.

Any change to this file must be coordinated — it is the shared namespace.

## Reference tables (`ref`) — defined in `model/common/reference.yaml`

Cross-cutting lookups every area may reference by name:

`SourceSystem`, `RecordStatus`, `UnitOfMeasure`, `UnitOfMeasureClass`, `Currency`,
`CurrencyRegion`, `Country`, `Region`, `Continent`, `Language`, `TimeZone`,
`Severity`, `Priority`, `ApprovalStatus`, `TransactionType`, `AdjustmentReason`,
`YesNoFlag`, `MeasurementSystem`, `HazardClass`, `ComplianceStandard`.

The full `ref` set is larger (~95); anything else in `reference.yaml` is fair game to
reference too, but the twenty above are guaranteed to exist.

## Conformed dimensions (`dim`) — defined in `model/common/conformed.yaml`

| Dimension | Business key | Notes |
|---|---|---|
| `Date` | `DateKey` (`int`, yyyymmdd) | Special: explicit `int` key, not identity |
| `TimeOfDay` | `TimeOfDayKey` (`int`, seconds from midnight) | Special: explicit key |
| `Product` | `ProductNumber` | SCD2; attaches to `ProductTaxonomy` hierarchy |
| `Plant` | `PlantCode` | SCD2; attaches to `GeographyTaxonomy` + `OrganizationTaxonomy` |
| `Site` | `SiteCode` | Sub-location within a plant |
| `WorkCenter` | `WorkCenterCode` | SCD2 |
| `Employee` | `EmployeeNumber` | SCD2 |
| `Customer` | `CustomerNumber` | SCD2 |
| `Supplier` | `SupplierNumber` | SCD2 |
| `Asset` | `AssetNumber` | SCD2 |
| `GlAccount` | `GlAccountNumber` | |
| `CostCenter` | `CostCenterCode` | |
| `BusinessUnit` | `BusinessUnitCode` | |

## Conformed hierarchies — defined in `model/common/conformed.yaml`

| Hierarchy | Levels (top → bottom) |
|---|---|
| `ProductTaxonomy` | `ProductDivision`, `ProductLine`, `ProductFamily`, `ProductSubfamily` |
| `GeographyTaxonomy` | `GeoContinent`, `GeoCountry`, `GeoStateProvince`, `GeoCity`, `GeoPostalCode` |
| `OrganizationTaxonomy` | `OrgEnterprise`, `OrgDivision`, `OrgRegion`, `OrgBusinessUnit` |
| `CalendarTaxonomy` | `CalendarYear`, `CalendarQuarter`, `CalendarMonth`, `CalendarWeek` |
| `FiscalTaxonomy` | `FiscalYear`, `FiscalQuarter`, `FiscalPeriod` |
| `AccountTaxonomy` | `AccountCategory`, `AccountGroup`, `AccountSubGroup` |

Each hierarchy level is a real table. Leaf dimensions attach to the **bottom** level.

## Fact conventions

Facts declare `date_roles` / `time_roles` rather than referencing `Date` / `TimeOfDay`
directly. `date_roles: [Order, Ship]` produces `OrderDateKey` and `ShipDateKey`.

## Naming rules to avoid collisions between subject areas

Every table defined in a subject-area file **must** be unique across the entire model.
When a concept exists in more than one area, prefix it with the area's domain noun:
`QualityInspectionType`, `MaintenanceWorkType`, `LogisticsServiceLevel` — not a bare
`InspectionType` / `WorkType` / `ServiceLevel`.
