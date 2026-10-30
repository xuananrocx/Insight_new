# ruff: noqa: F811
from tests.test_accounts_access import env, actor  # noqa: F401
from tests.test_api_models import create
from src.db import metadata_db as db
from src.core import accounts as a


def seed(env):
    team = create(env.admin, scope='team', model_ids=('m',))
    private = create(env.alice, model_ids=('m',))
    for owner, pid, scene, success in [(env.alice_id,team,'qa_chat',True),(env.alice_id,team,'qa_chat',False),(env.bob_id,team,'test',True),(env.alice_id,private,'qa_chat',False)]:
        with actor(owner):
            db.insert_ai_call_log(provider='api',provider_id=pid,model='m',scene=scene,success=success,messages=[{'role':'user','content':'PRIVATE_CONTENT'}],response_text='PRIVATE_ANSWER')
    return team


def test_users_totals_filters_and_privacy(env):
    team=seed(env)
    url='/api/v1/admin/api-stats/users'
    assert env.bob.get(url).status_code == 403
    response=env.admin.get(url)
    assert response.status_code == 200, response.text
    data=response.json()
    assert data['summary']['calls']==2
    assert data['summary']['failures']==1
    assert data['summary']['active_users']==1
    assert data['total']==1
    assert data['items'][0]['user_key']==env.alice_id
    assert 'PRIVATE_' not in response.text
    assert env.admin.get(url,params={'include_tests':True}).json()['summary']['calls']==3
    assert env.admin.get(url,params={'provider_id':team,'user_key':env.alice_id}).json()['items'][0]['model']=='m'
    assert env.admin.get(url,params={'search':'no-such-user'}).json()['total']==0
    assert env.admin.get(url,params={'model':'no-such-model'}).json()['summary']['calls']==0
    assert env.admin.get(url,params={'page':0}).status_code==422
    assert env.admin.get(url,params={'sort':'calls; DROP TABLE auth_users'}).status_code==422


def test_unknown_users_and_out_of_range_page(env):
    seed(env)
    with actor(env.admin_id):
        a.execute("DELETE FROM auth_objects WHERE kind='log'")
    data=env.admin.get('/api/v1/admin/api-stats/users',params={'page':999}).json()
    assert data['page']==1
    assert data['items'][0]['user_key']=='unknown'
    assert data['summary']['active_users']==0


def test_many_users_are_paged_stably_and_date_filtered(env):
    team = seed(env)
    with actor(env.admin_id):
        for i in range(25):
            uid=f'stats-{i:02d}'
            a.execute("INSERT INTO auth_users(id,username,password_hash,created_at) VALUES (?,?,?,?)", (uid,uid,'not-a-login-hash',1))
            db.insert_ai_call_log(provider='api',provider_id=team,owner_id=uid,model='m',scene='qa_chat',messages=[])
        old=db.insert_ai_call_log(provider='api',provider_id=team,owner_id=env.bob_id,model='old',scene='qa_chat',messages=[])
        a.execute('UPDATE ai_call_logs SET created_at=1 WHERE id=?',(old,))
    url='/api/v1/admin/api-stats/users'
    first=env.admin.get(url).json()
    second=env.admin.get(url,params={'page':2}).json()
    assert first['total']==26
    assert len(first['items'])==20 and len(second['items'])==6
    assert not ({r['user_key'] for r in first['items']} & {r['user_key'] for r in second['items']})
    assert first['summary']['calls']==27
    assert env.admin.get(url,params={'sort':'failures'}).json()['items'][0]['user_key']==env.alice_id
