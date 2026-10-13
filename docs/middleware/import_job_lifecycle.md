# Middleware import job lifecycle

## Tikslas

Apibrėžti standartinį importų vykdymo ciklą middleware sluoksnyje, kad:

- importai būtų atsekami
- pakartojimai būtų saugūs (idempotent)
- klaidos būtų valdomos be rankinio chaos'o

## Minimalus domeno modelis (middleware DB)

- `source_batch`
  - `id`, `source_system`, `source_reference`, `received_at`, `checksum`, `status`
- `staging_record`
  - `id`, `batch_id`, `external_key`, `raw_payload`, `raw_hash`, `status`, `error_code`
- `normalize_result`
  - `id`, `staging_record_id`, `normalized_payload`, `normalized_hash`, `validation_errors`
- `import_job`
  - `id`, `source_system`, `mode` (`dry_run|apply`), `status`, `started_at`, `finished_at`, `triggered_by`
- `import_job_item`
  - `id`, `job_id`, `entity_type`, `external_id`, `target_key`, `action` (`create|update|skip|error`), `message`
- `import_audit_event`
  - `id`, `job_id`, `level`, `event_type`, `payload`, `created_at`
- `dead_letter_item`
  - `id`, `staging_record_id`, `reason`, `payload`, `created_at`, `reprocessed_at`

## Statusai

## Batch/staging statusai

- `received`
- `parsed`
- `normalized`
- `validated`
- `ready_for_import`
- `failed`

## Job statusai

- `queued`
- `running`
- `completed`
- `completed_with_errors`
- `failed`
- `cancelled`

## Rekomenduojama vykdymo seka

1. `receive`:
   - priimti šaltinio failą/API snapshot
   - įrašyti `source_batch`
2. `staging`:
   - išskaidyti į `staging_record`
   - apskaičiuoti `raw_hash`
3. `normalize`:
   - mapinti į tikslinį kontraktą
   - sukurti `normalize_result`
4. `validate`:
   - schema + verslo taisyklės
   - atskirti `ready` ir `dead_letter`
5. `dry_run` (privalomas prieš apply):
   - simuliuoti upsert veiksmus
   - paskaičiuoti `create/update/skip/error`
6. `apply`:
   - deterministic upsert į platformą
   - per-entity audit (`import_job_item`)
7. `reconcile`:
   - patikra po importo (sample check, count check, consistency)

## Idempotency taisyklės

- Kiekvienam normalized įrašui turi būti:
  - stabilus `external_id`
  - stabilus `natural_key` (pvz. `sku`)
  - `normalized_hash`
- Jei `normalized_hash` nepasikeitė nuo paskutinio sėkmingo importo:
  - action = `skip_no_change`
- Upsert tvarka:
  - pirma pagal external id
  - fallback pagal natural key
  - radus pagal fallback ir trūkstant external id, užpildyti mapping

## Retry strategija

- Transient klaidos (`network`, `timeout`, `429`, `5xx`):
  - retry su exponential backoff
  - max bandymų skaičius (pvz. 5)
- Permanent klaidos (`schema`, `missing_required`, `invalid_mapping`):
  - be automatinio retry
  - kelti į `dead_letter_item`

## Dead-letter tvarkymas

- `dead_letter_item` privalo saugoti:
  - klaidos kodą
  - žmogui skaitomą priežastį
  - original payload
  - susijusį job/batch
- Reprocess:
  - po mapping/fix pataisymo leidžiamas perleidimas tik pasirinktų item'ų

## Observability ir KPI

- Techniniai:
  - `job_duration_seconds`
  - `records_per_second`
  - `retry_count`
  - `dead_letter_count`
- Kokybės:
  - `validation_error_rate`
  - `mapping_miss_rate`
  - `idempotent_skip_rate`
  - `unexpected_update_rate`

## Operacinės taisyklės

- `apply` režimas leidžiamas tik po sėkmingo `dry_run`.
- Kiekvienas `apply` job turi turėti snapshot nuorodą į source batch.
- Kiekvienam job turi būti sugeneruota trumpa ataskaita:
  - kiek `create/update/skip/error`
  - top klaidos pagal kodą
  - mapping miss sąrašai (`category`, `brand`, `warehouse`, `tax_class`)

## Minimali API tarp middleware ir platformos

- `POST /import/catalog/categories`
- `POST /import/catalog/brands`
- `POST /import/catalog/products`
- `POST /import/catalog/stock`

Kiekvienas request turi:

- `idempotency_key`
- `source_system`
- `job_id`
- `batch_id`
- `records[]`

Atsakymas turi grąžinti:

- `accepted`
- `created`
- `updated`
- `skipped`
- `errors[]` su `external_id` ir `error_code`
