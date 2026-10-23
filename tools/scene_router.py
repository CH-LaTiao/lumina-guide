"""Conservative question-to-template routing; user can override the scene."""
def scene_for_question(question):
    q=question.strip().lower()
    groups=(
        ('traffic_signal',('红绿灯','交通灯','过马路','过街','路口信号')),
        ('stairs',('楼梯','台阶','扶梯','下楼','上楼','跌落')),
        ('metro',('地铁','站台','几号线','换乘','号线','闸机')),
        ('read_sign',('读字','读一下','写的什么','写着什么','文字','指示牌')),
        ('find_target',('厕所','洗手间','卫生间','找','入口','出口','在哪里','在哪')),
        ('obstacle',('障碍','绕开','挡路','怎么走','通行','能走')),
    )
    return next((scene for scene,words in groups if any(w in q for w in words)),'general')
