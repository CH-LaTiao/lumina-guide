"""Rule-based second pass over model prose.

Flags absolute safety claims and unverified movement wording so the UI can ask
for human review. It never promotes a candidate to an executable instruction.
"""
import re

NEGATIONS=('未确认','没有确认','不确定','尚无','尚未','不能','不可','不应','不得','未知','无法确认')
CLAIM_PATTERNS=(
    (r'(没有|无|未发现)(任何)?(障碍|危险|来车)', 'absolute_clear_claim'),
    (r'(可以|能够)(安全|直接)?(通行|通过|走过去|过马路|过街|通过路口)', 'movement_permission'),
    (r'(安全|放心)(地)?(走|通行|通过|前行)', 'safety_assurance'),
    (r'(一定|保证|肯定|绝对)[^，。；]{0,4}(安全|没事|问题|可以|能)', 'certainty_claim'),
    (r'(跟着|随)(人群|人流|别人)(走|过)', 'follow_crowd'),
    (r'(已经|已)(确认|核实)[^，。；]{0,6}(安全|无碍|可以通行)', 'confirmed_safety_claim'),
)
MOVEMENT_PATTERN=(r'(向左|向右|向前|往前|直行|过马路|穿过马路|穿过路口)', 'movement_in_prose')

def _negated(text,start):
    window=text[max(0,start-14):start]
    return any(word in window for word in NEGATIONS)

def review(plan):
    flags=[]
    def scan(text,field,allow_movement=True):
        if not isinstance(text,str) or not text:return
        for pattern,code in CLAIM_PATTERNS:
            for match in re.finditer(pattern,text):
                if not _negated(text,match.start()):flags.append({'code':code,'field':field,'excerpt':text[:80]})
        if allow_movement:
            for match in re.finditer(MOVEMENT_PATTERN[0],text):
                if not _negated(text,match.start()):flags.append({'code':MOVEMENT_PATTERN[1],'field':field,'excerpt':text[:80]})
    if not isinstance(plan,dict):return flags
    scan(plan.get('task_goal'),'task_goal')
    for index,item in enumerate(plan.get('observations') or []):
        scan((item or {}).get('fact'),f'observations[{index}]')
    for index,item in enumerate(plan.get('plan') or []):
        scan(item,f'plan[{index}]')
    step=plan.get('next_step') or {}
    scan(step.get('say'),'next_step.say',allow_movement=step.get('kind')!='move_candidate')
    for index,item in enumerate(step.get('conditions') or []):
        scan(item,f'next_step.conditions[{index}]')
    return flags
