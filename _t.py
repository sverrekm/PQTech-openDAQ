import importlib.util
spec = importlib.util.spec_from_file_location('o', 'oppdatering.py')
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)
print('git.pqtech.no ->', o._forge('git.pqtech.no'))
p, e, r = o._eigar_repo('https://git.pqtech.no/sverre/pq-tech-opendaq')
print('tarball ->', o._tarball_url(p, e, r, 'main'))
