import unittest
import pipeline_scope

class ScopeTests(unittest.TestCase):
 def test_only_supported_checked_alerts_are_ready_and_others_accounted_for(self):
  def item(id,state,status,kind=None,decision=None):
   return {'change_id':id,'state':state,'decision':decision,'item':{'id':id},'assessment':{'status':status,'interpretation':{'change_type':kind}} if status else None}
  entries=[item('ready','completed','not_modeled','stops_skipped','yes'),item('unknown','completed','needs_clarification'),item('future','not_checked',None),item('unsupported','completed','not_modeled','reroute_via_route','yes'),item('delay','completed','not_recommended_current_disruption')]
  manual=[{'id':'edit1','active':True,'reflection':'not_reflected'},{'id':'edit2','active':True,'reflection':'unknown'}]
  scope=pipeline_scope.summarize({'items':entries,'stale':False},manual)
  self.assertEqual([x['change_id'] for x in scope['supported']],['ready'])
  self.assertEqual({x['change_id'] for x in scope['blocked']},{'unknown','future','unsupported'})
  self.assertEqual(scope['missing'],['edit2'])
if __name__=='__main__':unittest.main()
