"""Test de humo de Admin en SQLite TEMPORAL (no sustituye PostgreSQL).

Requiere instalar apps/BackendWebFlask/requirements.txt. No crea datos en
la base real; usa un archivo temporal para verificar formularios y reglas.
"""
import io,os,pickle,sys,uuid,datetime,tempfile,zipfile
from pathlib import Path
_scratch = tempfile.TemporaryDirectory(prefix='pef-admin-smoke-')
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(_scratch.name) / 'test.db')
os.environ['FRONTEND_DEMO_MODE']='true'
os.environ['SESSION_SECRET_KEY']='test-key'
# Administration uploads (examples, notices, weights) go to a local folder inside the temporary dir.
os.environ['ADMIN_STORAGE_BACKEND']='local'
os.environ['ADMIN_STORAGE_DIR']=str(Path(_scratch.name) / 'admin_storage')
web_root = Path(__file__).resolve().parents[1] / 'apps' / 'BackendWebFlask'
sys.path.insert(0, str(web_root))
import config
config.Config.SQLALCHEMY_ENGINE_OPTIONS={}
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import INET,JSONB
@compiles(INET,'sqlite')
def inetcompiler(element,compiler,**kw):return 'VARCHAR(64)'
@compiles(JSONB,'sqlite')
def jsoncompiler(element,compiler,**kw):return 'JSON'
import app
from extensions import db
from services.admin_local.models import (Base,Institution,Role,User,UserRole,CatInstrumentCategory,CatInstrumentCycleStatus,
                                          InstrumentFamily,CatOperationPhase,CatProcedureType,Kit,KitItem,OperatingRoom)
from controllers import routes
class FakeResp:
    status_code=200;ok=True;cookies={}
    role='it_admin'
    def json(self):return {'valid':True,'user':{'id':str(actor_id),'name':'Smoke Admin','email':'smoke@example.com','institution_id':str(inst_id), 'ui_preferences':{'locale':'en'},'roles':[{'code':self.role}]}}
def as_role(role):
    """AuthService /verify of the smoke actor with `role` (no other AuthService call is expected)."""
    def fake_auth(method,path,token=None,**kwargs):
        if path=='/verify':
            response=FakeResp(); response.role=role; return response
        raise AssertionError('unexpected external dependency: '+path)
    routes._auth_request=fake_auth
as_role('it_admin')
uid=lambda:uuid.uuid4()
inst_id=uid();actor_id=uid();role_id=uid();cat_id=uid()
other_inst_id=uid();other_role_id=uid()
with app.app.app_context():
    Base.metadata.drop_all(db.engine,checkfirst=True)
    Base.metadata.create_all(db.engine)
    from sqlalchemy import text as sql
    db.session.execute(sql('CREATE UNIQUE INDEX ux_smoke_family_code ON instrument_family (code)'))
    db.session.commit()
    now=datetime.datetime.now(datetime.timezone.utc)
    db.session.add_all([
        Institution(id=inst_id,name='Test hospital',active=True),
        Role(id=role_id,code='it_admin',description='Admin',institution_id=inst_id),
        # RF-AD-06: the other fixed product roles of the institution, plus another institution's role.
        *[Role(id=uid(),code=code,description=code,institution_id=inst_id)
          for code in ('station_operator','spd_supervisor','or_charge')],
        Institution(id=other_inst_id,name='Other hospital',active=True),
        Role(id=other_role_id,code='it_admin',description='Other admin',institution_id=other_inst_id),
        User(id=actor_id, name='Smoke Admin',active=True,email='smoke@example.com', password_hash='test',created_at=now, updated_at=now,institution_id=inst_id),
        UserRole(id=uid(),user_id=actor_id,role_id=role_id,assigned_at=now),
        CatInstrumentCategory(id=cat_id,code='hemostasis',name='Hemostasis'),
        CatInstrumentCycleStatus(id=uid(),code='available',name='Available'),
        CatOperationPhase(id=uid(),code='initial',name='Initial',active=True),
        CatProcedureType(id=uid(),code='test_proc',name='Test procedure')
    ])
    db.session.commit()
client=app.app.test_client();client.set_cookie('access_token','fake-but-verified')

def test(m,path,expected,**kwargs):
    if m not in ('GET','HEAD'):
        # MS-04: the portal's CSRF token, as the page would send it (bound to the signed-in user).
        kwargs.setdefault('headers',{})['X-CSRF-Token']=client.get('/csrf-token').get_json()['csrf_token']
    r=client.open(path,method=m,**kwargs)
    print(m,path,r.status_code,'expected',expected,r.location or '')
    assert r.status_code==expected,(m,path,r.status_code,r.data[:1300])
    return r

test('GET','/admin/instrument-families',200)
test('GET','/admin/instrument-families/new',200)
test('POST','/admin/instrument-families/new',302,data={'code':'TEST_FAMILY','name':'Test forceps','category':str(cat_id),'status':'active'})
test('GET','/admin/instrument-families?search=TEST_FAMILY',200)
test('POST','/admin/instrument-families/new',409,data={'code':'TEST_FAMILY','name':'Duplicate','category':str(cat_id),'status':'active'})
# Verify persisted, audit recorded
from services.admin_local.models import AccessAudit
with app.app.app_context():
 f=db.session.query(InstrumentFamily).filter_by(code='TEST_FAMILY').one()
 assert f.name=='Test forceps'
 ev=db.session.query(AccessAudit).filter_by(action='CREATE_INSTRUMENT_FAMILY').one()
 print('family persistent',f.id,'audit persistent',ev.id)
 family_id=str(f.id)
test('GET',f'/admin/instrument-families/{family_id}/edit',200)
test('POST',f'/admin/instrument-families/{family_id}/edit',302,data={'name':'Test forceps updated','category':str(cat_id),'status':'active','identify_text':'manual'})
# RACI: pieces and kits belong to the SPD supervisor; Administration has no CRUD on them.
test('GET','/admin/kits',403)
test('POST','/admin/kits/new',403,data={'name':'KIT-X','status':'active','instrument_family[]':[family_id],'expected_quantity[]':['3']})
test('POST','/admin/instruments/new',403,data={'internal_code':'INS-X','instrument_family':family_id,'cycle_status':'available'})
as_role('spd_supervisor')
test('GET','/admin/kits',200)
test('POST','/admin/kits/new',302,data={'name':'KIT-A','status':'active','instrument_family[]':[family_id],'expected_quantity[]':['3']})
test('POST','/admin/instruments/new',302,data={'internal_code':'INS-001','instrument_family':family_id,
     'cycle_status':'available','active_status':'active'})
as_role('it_admin')
with app.app.app_context():
  from services.admin_local.models import Kit, Instrument
  kit = db.session.query(Kit).filter_by(name='KIT-A').one()
  assert db.session.query(Instrument).filter_by(internal_code='INS-001').count()==1
  procedure_phase=db.session.query(CatOperationPhase).one()
test('POST','/admin/procedures/new',302,data={'code':'test_proc_added','name':'Added procedure',
    'kit[]':[str(kit.id)], 'kit_row_index[]':['0'], 'technique_label[]':['Laparoscopic'],
    'is_default[]':['0'], 'kit_active[]':['0'], 'phase[]':[str(procedure_phase.id)],
    'phase_row_index[]':['0'],'sort_order[]':['1'], 'count_required[]':['0'],'phase_active[]':['0']})
with app.app.app_context():
  from services.admin_local.models import ProcedureKit, ProcedurePhase
  proc=db.session.query(CatProcedureType).filter_by(code='test_proc_added').one()
  assert db.session.query(ProcedureKit).filter_by(procedure_type_id=proc.id).count()==0  # D-4: kit links are SPD's
  assert db.session.query(ProcedurePhase).filter_by(procedure_type_id=proc.id).count()==1
  print('Instrument, procedure, phases persisted (no kit links from Administration):',proc.id)
test('GET','/admin/users',200)
test('GET','/admin/roles',200)
test('GET','/admin/configuration',200)
test('GET','/admin/vision-models',200)
# Las consultas de la bitácora y del dashboard contienen SQL propio de PostgreSQL.
# Se probarán contra PostgreSQL real: SQLite no acepta AT TIME ZONE/host/string_agg.
print('PASS smoke')
# Stronger business checks (these operations use local SQLAlchemy, no API calls).
test('POST','/admin/instrument-families/new',400,data={'code':'BAD','name':'Bad family','category':'not-a-uuid'})
# RF-AD-06 (ADMIN-01.1): role codes are fixed. No create/delete route; descriptions are edited locally.
test('GET','/admin/roles/new',404)
test('POST','/admin/roles/new',404,data={'code':'nurse_reviewer','description':'Nurse review'})
test('POST',f'/admin/roles/{role_id}/delete',404)
test('POST',f'/admin/roles/{role_id}/edit',302,data={'description':'IT administrator (smoke)'})
test('POST',f'/admin/roles/{role_id}/edit',400,data={'description':'   '})
test('GET',f'/admin/roles/{other_role_id}/edit',404)
test('POST',f'/admin/roles/{other_role_id}/edit',404,data={'description':'Hijacked'})
roles_page=test('GET','/admin/roles',200).get_data(as_text=True)
assert 'New Role' not in roles_page and 'Other admin' not in roles_page and 'or_charge' in roles_page
# RF-AD-05 (ADMIN-01.2): users are administered in-process on the identity tables AuthService reads.
users_form=test('GET','/admin/users/new',200).get_data(as_text=True)
assert all(f'value="{code}"' in users_form for code in ('station_operator','spd_supervisor','it_admin','or_charge'))
test('POST','/admin/users/new',302,data={'name':'Second Admin','email':'new-user@example.com','roles[]':['it_admin'],'status':'active','password':'SuperSecure123','confirm_password':'SuperSecure123'})
test('POST','/admin/users/new',409,data={'name':'Dup','email':'NEW-USER@example.com','roles[]':['it_admin'],'password':'SuperSecure123','confirm_password':'SuperSecure123'})
test('POST','/admin/users/new',400,data={'name':'Short','email':'short@example.com','roles[]':['it_admin'],'password':'short','confirm_password':'short'})
# RF-AD-03 selection catalogs, RF-AD-04 privacy notice, RF-AD-10 parameters.
test('POST','/admin/selection-catalogs/reasons',302,data={'code':'smoke_reason','name':'Smoke reason'})
test('POST','/admin/privacy-notices/new',302,data={'document':(io.BytesIO(b'%PDF-1.4 smoke'),'aviso.pdf'),'version':'smoke-1','effective_at':'2026-11-01','status':'active'},content_type='multipart/form-data')
# RF-AD-08: upload a (synthetic) checkpoint, then map its classes and publish.
def checkpoint(names):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as zf: zf.writestr('best/data.pkl',pickle.dumps({'model':{'names':names,'yaml':{'yaml_file':'yolov8n.yaml'}}},protocol=2))
    return data.getvalue()
test('POST','/admin/vision-models/new',302,data={'version_tag':'ai-test-1','weights':(io.BytesIO(checkpoint({0:'TEST_FAMILY'})),'best.pt')},content_type='multipart/form-data')
test('POST','/admin/configuration/operating-rooms/new',302,data={'code':'OR-1','name':'Room One','status':'active'})
with app.app.app_context():
 from services.admin_local.models import Role,User,YoloModel,OperatingRoom,PrivacyNoticeVersion,CatDiscrepancyReason
 new_user=db.session.query(User).filter_by(email='new-user@example.com').one()
 from werkzeug.security import check_password_hash
 assert check_password_hash(new_user.password_hash,'SuperSecure123') and new_user.institution_id==inst_id
 assert db.session.query(PrivacyNoticeVersion).filter_by(version='smoke-1').count()==1
 assert db.session.query(CatDiscrepancyReason).filter_by(code='smoke_reason').count()==1
 pending_model=db.session.query(YoloModel).filter_by(version_tag='ai-test-1').one()
 assert pending_model.class_names=={'0':'TEST_FAMILY'} and not pending_model.active
 assert db.session.query(Role).filter_by(code='nurse_reviewer').count()==0
 assert db.session.query(Role).filter_by(institution_id=inst_id).count()==4
 assert db.session.get(Role,role_id).description=='IT administrator (smoke)'
 assert db.session.get(Role,other_role_id).description=='Other admin'
 assert db.session.query(AccessAudit).filter_by(action='UPDATE_ROLE',resource_id=role_id).count()==1
 model=db.session.query(YoloModel).filter_by(version_tag='ai-test-1').one()
 room=db.session.query(OperatingRoom).filter_by(code='OR-1').one()
 print('Fixed roles, role description, model, room persisted:',model.id,room.id)
test('POST',f'/admin/vision-models/{model.id}/edit',302,data={f'class_0':family_id,'confirm_missing_families':'1'})
test('POST','/admin/configuration/capture-stations/new',302,data={'name':'Camera-1','operating_room':str(room.id),'status':'active',
     'roi_x':'10','roi_y':'10','roi_w':'100','roi_h':'100','roi_frame_w':'640','roi_frame_h':'480'})
from services.admin_local.models import CaptureStation,ModelClass
with app.app.app_context():
 assert db.session.query(CaptureStation).filter_by(name='Camera-1').count()==1
 assert db.session.query(ModelClass).filter_by(model_id=model.id).count()==1
 print('Station + vision class mapping persisted')
# Isolation of role permissions.
class SupervisorResp(FakeResp):
 def json(self):
  d=super().json();d['user']['roles']=[{'code':'spd_supervisor'}];return d
routes._auth_request=lambda method,path,token=None,**kwargs:SupervisorResp() if path=='/verify' else None
test('GET','/admin/users',403)
test('GET','/admin/privacy-notices',403)
test('GET','/admin/kits',200)
client.delete_cookie('access_token')
test('GET','/admin/users',302)
print('PASS role restrictions and session requirement')
# Windows keeps the SQLite file locked while the pool holds connections.
with app.app.app_context():
    db.session.remove()
    db.engine.dispose()
_scratch.cleanup()
