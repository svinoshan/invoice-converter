-- =====================================================================
-- Invoice Converter - Supabase PostgreSQL schema
-- Run this in the Supabase SQL Editor (Dashboard > SQL Editor).
-- =====================================================================

-- UUID generation helper (gen_random_uuid) is built into recent Postgres.
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------

create table if not exists public.invoices (
    id                uuid primary key default gen_random_uuid(),
    client_name       text,
    invoice_no        text unique,
    invoice_date      date,
    currency          text,
    description_text  text,
    total_amount      numeric(12, 2),
    pdf_file_name     text,
    pdf_file_url      text,
    raw_text          text,
    extraction_method text,
    status            text default 'saved',
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Auto-update updated_at on invoices
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

drop trigger if exists trg_invoices_updated_at on public.invoices;
create trigger trg_invoices_updated_at
    before update on public.invoices
    for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------
-- Row level security
-- The service role key bypasses RLS. Policies below keep things explicit
-- so the tables stay readable/writable only via the service role.
-- ---------------------------------------------------------------------

alter table public.invoices enable row level security;

drop policy if exists "service_role full access invoices" on public.invoices;
create policy "service_role full access invoices"
    on public.invoices for all
    to service_role using (true) with check (true);

-- ---------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------

create index if not exists idx_invoices_invoice_no
    on public.invoices (invoice_no);

create index if not exists idx_invoices_created_at
    on public.invoices (created_at desc);

-- ---------------------------------------------------------------------
-- Storage bucket for the original PDF files
-- ---------------------------------------------------------------------

insert into storage.buckets (id, name, public)
values ('invoice-pdfs', 'invoice-pdfs', true)
on conflict (id) do nothing;
