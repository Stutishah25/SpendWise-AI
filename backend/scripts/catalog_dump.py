import sys, re, psycopg
url = sys.argv[1]
q = {
"columns": """select c.relname, a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
   coalesce(pg_get_expr(d.adbin,d.adrelid),''), a.attidentity
   from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace
   left join pg_attrdef d on d.adrelid=a.attrelid and d.adnum=a.attnum
   where n.nspname='public' and c.relkind='r' and a.attnum>0 and not a.attisdropped and c.relname<>'alembic_version'""",
"constraints": """select c.relname, k.contype, pg_get_constraintdef(k.oid)
   from pg_constraint k join pg_class c on c.oid=k.conrelid join pg_namespace n on n.oid=c.relnamespace
   where n.nspname='public' and c.relname<>'alembic_version' and k.contype in ('p','f','u','c')""",
"indexes": """select tablename, indexdef from pg_indexes where schemaname='public' and tablename<>'alembic_version'
   and indexname not in (select conname from pg_constraint)""",
"enums": """select t.typname, string_agg(e.enumlabel, ',' order by e.enumsortorder) from pg_type t
   join pg_enum e on e.enumtypid=t.oid group by 1""",
"triggers": """select c.relname, regexp_replace(pg_get_triggerdef(t.oid), 'CREATE (CONSTRAINT )?TRIGGER \\S+', 'CREATE \\1TRIGGER')
   from pg_trigger t join pg_class c on c.oid=t.tgrelid where not t.tgisinternal""",
"functions": """select proname, md5(regexp_replace(prosrc,'\\s+',' ','g')) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
   where n.nspname='public' and prokind='f'""",
"views": "select viewname, regexp_replace(definition,'\\s+',' ','g') from pg_views where schemaname='public'",
"seed": "select code,name,symbol,minor_units,default_locale,is_enabled from currencies order by 1",
}
out = {}
with psycopg.connect(url) as con:
    for k, s in q.items():
        rows = con.execute(s).fetchall()
        norm = []
        for r in rows:
            r = [str(x) for x in r]
            if k == "indexes": r[1] = re.sub(r"INDEX \S+ ON", "INDEX ON", r[1])
            norm.append(" | ".join(r))
        out[k] = sorted(norm)
for k, v in out.items():
    print("##", k)
    print("\n".join(v))
