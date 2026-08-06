-- =====================================================================
-- Invoice Converter - Supabase PostgreSQL schema
-- Run this in the Supabase SQL Editor (Dashboard > SQL Editor).
--
-- The app stores only EXTRACTED INVOICE RECORDS in Supabase. PDF files are
-- intentionally NOT uploaded to Storage. Excel files are generated from
-- these records.
-- =====================================================================

-- UUID generation helper (gen_random_uuid) is built into recent Postgres.
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- Main table: one row per invoice
-- ---------------------------------------------------------------------

create table if not exists public.invoice_records (
    id                uuid primary key default gen_random_uuid(),
    client            text,
    invoice_no        text not null,
    invoice_date      date,
    description       text,
    sub_total         numeric(12, 2),
    total_amount      numeric(12, 2),
    currency          text,
    source_file_name  text,
    raw_text          text,
    extraction_status text default 'processed',
    reviewed          boolean default false,
    created_at        timestamp with time zone default now(),
    updated_at        timestamp with time zone default now(),
    constraint invoice_records_invoice_no_unique unique (invoice_no)
);

-- ---------------------------------------------------------------------
-- Migration for an existing invoice_records table (missing columns)
-- ---------------------------------------------------------------------

alter table public.invoice_records
    add column if not exists sub_total numeric(12, 2);

alter table public.invoice_records
    add column if not exists total_amount numeric(12, 2);

alter table public.invoice_records
    add column if not exists currency text;

alter table public.invoice_records
    add column if not exists description text;

alter table public.invoice_records
    add column if not exists source_file_name text;

alter table public.invoice_records
    add column if not exists extraction_status text default 'processed';

alter table public.invoice_records
    add column if not exists reviewed boolean default false;

-- If an old table carried an 'amount' column, keep it untouched - the app
-- uses total_amount for the final Excel export.
-- If an old table carried a 'currancy' column, keep it untouched - the app
-- uses currency internally and exports it as "Currency" in Excel.

-- ---------------------------------------------------------------------
-- Auto-update updated_at on invoice_records
-- ---------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_invoice_records_updated_at on public.invoice_records;
create trigger trg_invoice_records_updated_at
    before update on public.invoice_records
    for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------
-- Row level security
-- The service role key bypasses RLS. Policies below keep things explicit
-- so the table stays readable/writable only via the service role.
-- ---------------------------------------------------------------------

alter table public.invoice_records enable row level security;

drop policy if exists "service_role full access invoice_records" on public.invoice_records;
create policy "service_role full access invoice_records"
    on public.invoice_records for all
    to service_role using (true) with check (true);

-- ---------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------

create index if not exists idx_invoice_records_invoice_no
    on public.invoice_records (invoice_no);

create index if not exists idx_invoice_records_invoice_date
    on public.invoice_records (invoice_date desc);

create index if not exists idx_invoice_records_client
    on public.invoice_records (client);

create index if not exists idx_invoice_records_status
    on public.invoice_records (extraction_status);
