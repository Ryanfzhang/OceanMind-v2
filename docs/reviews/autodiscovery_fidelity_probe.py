import ast, math, json
from pathlib import Path
from types import SimpleNamespace
import sys
# Freeze the historical OceanX baseline used by this pre-change audit.
sys.path.insert(0, "/tmp/oceanx-tree-goal-retest-20260909-235023/tree/src")
from oceanx.exploration import _recommend, _statistics
p=Path('/tmp/autodiscovery-audit/src/mcts.py')
a=ast.parse(p.read_text()); selected=[n for n in a.body if isinstance(n,ast.FunctionDef) and n.name in ('progressive_widening','ucb1')]
ns={'np':SimpleNamespace(sqrt=math.sqrt,log=math.log)}
exec(compile(ast.Module(body=selected,type_ignores=[]),str(p),'exec'),ns)
def n(key,visits=0,value=0):
 return SimpleNamespace(id=key,visits=visits,value=value,children=[],parent=None,has_untried_experiments=lambda:True)
r=n('root',1); a=n('A',1,.1); b=n('B');r.children=[a,b];a.parent=b.parent=r
of=ns['progressive_widening'](1,.5,1)(r,{})
t={'paused':False,'mode':'iterative','node_budget':12,'active_branch_id':'A','nodes':{'root':{'parent_id':None,'expandable':True},'A':{'parent_id':'root','expandable':True,'feedback':{'branch_status':'open','information_gain':.1}},'B':{'parent_id':'root','expandable':True}}}
ours=_recommend(t,_statistics(t))
assert of.id=='B' and ours=={'action':'expand','node_id':'A'}
# Root widening: visit growth permits a further broad direction in reference PW.
r.visits=9
assert ns['progressive_widening'](1,.5,1)(r,{}).id=='root'
# One shared observation recorded on multiple nodes increases aggregate attempts repeatedly.
t['nodes']['A']['feedback']['evidence_ids']=['same_observation'];t['nodes']['B']['feedback']={'branch_status':'open','information_gain':.1,'evidence_ids':['same_observation']}
assert _statistics(t)['root'][0]==2
print(json.dumps({'scope':'Synthetic selection probes, not a scientific performance benchmark','official_reference':'PW k=1 alpha=.5 C=1 (explicit paper-family strategy, NOT current CLI default)','first_probe':{'official_selected_parent':of.id,'oceanx_recommendation':ours},'official_root_widening_at_N9':'root','shared_evidence_two_feedback_nodes_root_attempts':_statistics(t)['root'][0]},indent=2))
