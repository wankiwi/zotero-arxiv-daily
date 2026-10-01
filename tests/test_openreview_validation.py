"""Validation workflow never invokes delivery/model/state code."""
from types import SimpleNamespace
import json
import pytest
from scripts.validate_openreview import validate, ProbeClient


def test_probe_logs_only_aggregate_public_counts(capsys):
    client=SimpleNamespace(last_status=200,calls=11,stage='login')
    seen=[]
    class Retriever:
        authenticated=False
        days=7
        def _authenticate(self,client):self.authenticated=True
        def _groups(self,*args):return [('TMLR','TMLR/-/Submission')]
        def _get(self,*args):
            return {'notes':[{'readers':['everyone'],'content':{'abstract':'DO_NOT_LOG'}},
                             {'readers':['private'],'content':{'abstract':'CONFIDENTIAL'}},
                             {'readers':['everyone'],'nonreaders':['excluded'],'content':{'abstract':'CONFIDENTIAL'}}]}
        def convert_to_paper(self,raw):seen.append(raw);return object()
    validate(Retriever(),client)
    output=capsys.readouterr().out
    assert 'CONFIDENTIAL' not in output and 'DO_NOT_LOG' not in output
    rows=[json.loads(line) for line in output.splitlines()]
    assert rows[-1]['status']=='success' and rows[-1]['public_sample_count']==5 and len(seen)==5
    assert all(row['public_count']==1 for row in rows[1:-1])


def test_probe_stops_before_expansion_without_public_sample(capsys):
    client=SimpleNamespace(last_status=200,calls=2,stage='login')
    r=SimpleNamespace(authenticated=True,days=7,_authenticate=lambda c:None,
        _groups=lambda *args:[('TMLR','TMLR/-/Submission')],_get=lambda *args:{'notes':[]})
    with pytest.raises(RuntimeError,match='expansion stopped'):validate(r,client)
    assert 'ICLR' not in capsys.readouterr().out


def test_probe_rejects_request_beyond_bound_or_origin():
    client=ProbeClient()
    try:
        with pytest.raises(RuntimeError,match='boundary'):client.get('https://example.org')
        client.calls=22
        with pytest.raises(RuntimeError,match='boundary'):client.get('https://api2.openreview.net/notes')
    finally:client.close()


@pytest.mark.parametrize('message,category',[
    ('expiresIn must be a string SENSITIVE','login_expiry_parameter_rejected'),
    ('Invalid username or password SENSITIVE','credentials_or_login_identifier_rejected'),
    ('Invalid email SENSITIVE','login_email_format_rejected'),
    ('Account not activated SENSITIVE','account_activation_required'),
    ('MFA required SENSITIVE','MFA_required'),
    ('SENSITIVE','unclassified_error'),
])
def test_provider_error_diagnostics_never_return_provider_text(message,category):
    from scripts.validate_openreview import safe_error_category
    response=SimpleNamespace(json=lambda:{'errors':[{'message':message}]})
    assert safe_error_category(response)==category
