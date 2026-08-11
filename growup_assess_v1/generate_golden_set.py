"""
Golden Set 生成脚本 — 3 个试点领域 × 20 对 QA
===============================================
领域: 机器学习 / 计算机网络 / 数据库系统
深度分布: depth=1 (14条) + depth=3 (2条) + depth=4 (2条) + depth=5 (2条) = 20条/领域

运行: python generate_golden_set.py
输出: golden_set/{domain}.json
"""

import json
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "golden_set")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def qa(qa_id, domain, depth, question, answer, concepts, chain=None, tags=None):
    """构造一条 golden QA"""
    return {
        "qa_id": qa_id,
        "domain": domain,
        "depth": depth,
        "parent_concept_chain": chain or [],
        "question": question,
        "reference_answer": answer,
        "golden_concepts": [
            {
                "canonical_name": c[0],
                "aliases": c[1],
                "in_answer": True,
                "note": c[2] if len(c) > 2 else "",
            }
            for c in concepts
        ],
        "tags": tags or [],
    }


# ══════════════════════════════════════════════════════
# 领域 1: 机器学习
# ══════════════════════════════════════════════════════

ML_QA = [
    qa("ml_001", "机器学习", 1,
       "什么是梯度下降？",
       "梯度下降是一种迭代优化算法，用于最小化损失函数。它通过沿损失函数梯度的反方向更新参数来逐步逼近最优解。在每次迭代中，算法计算当前参数下损失函数的梯度，然后乘以学习率作为步长进行参数更新。梯度下降有三个主要变体：批量梯度下降使用全部训练数据计算梯度，随机梯度下降（SGD）每次只用一个样本，小批量梯度下降则使用一小批样本。学习率是关键超参数，太大会导致震荡甚至发散，太小则收敛缓慢。在深度学习中，梯度下降通常与反向传播结合使用，反向传播利用链式法则高效计算梯度。",
       [
           ("梯度下降", ["gradient descent", "GD", "最速下降法"], "PRD 直接引用的归一化示例"),
           ("损失函数", ["loss function", "代价函数", "cost function"]),
           ("学习率", ["learning rate", "lr", "步长"]),
           ("随机梯度下降", ["stochastic gradient descent", "SGD"]),
           ("小批量梯度下降", ["mini-batch gradient descent", "MBGD"]),
           ("反向传播", ["backpropagation", "BP", "反向传播算法"]),
           ("链式法则", ["chain rule"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("ml_002", "机器学习", 1,
       "反向传播算法是如何工作的？",
       "反向传播是训练神经网络的核心算法，它通过链式法则高效计算损失函数对每一层权重的梯度。算法分为前向传播和反向传播两个阶段：前向传播时，输入数据逐层通过网络，每层计算加权求和并经过激活函数，最终输出预测值并计算损失。反向传播时，从输出层开始，利用链式法则逐层向后计算损失对每个参数的偏导数。具体来说，先计算输出层的误差信号（损失对输出的导数），然后逐层向后传播，每层的误差信号等于上一层误差信号乘以权重矩阵的转置再乘以激活函数的导数。梯度计算完成后，通过梯度下降更新参数。反向传播的效率远高于数值微分，是深度学习训练的基石。",
       [
           ("反向传播", ["backpropagation", "BP", "反向传播算法"]),
           ("链式法则", ["chain rule"]),
           ("损失函数", ["loss function", "代价函数"]),
           ("梯度下降", ["gradient descent", "GD"]),
           ("激活函数", ["activation function"]),
           ("前向传播", ["forward propagation", "前向计算"]),
           ("权重", ["weight", "权重矩阵"]),
       ],
       tags=["alias_heavy"]),

    qa("ml_003", "机器学习", 1,
       "什么是过拟合？如何防止？",
       "过拟合是指模型在训练数据上表现很好，但在未见过的测试数据上表现差的现象，本质是模型过度学习了训练数据中的噪声和特定模式而非通用规律。防止过拟合的常用方法包括：正则化（L1 正则化促进稀疏性，L2 正则化限制权重大小），Dropout 在训练时随机丢弃神经元，数据增强通过变换扩充训练集，早停法在验证集性能开始下降时停止训练，交叉验证帮助选择合适的模型复杂度，以及集成学习通过组合多个模型降低方差。模型复杂度也应适当控制，过于复杂的模型更容易过拟合。",
       [
           ("过拟合", ["overfitting", "过学习"]),
           ("正则化", ["regularization"]),
           ("L1正则化", ["L1 regularization", "Lasso", "L1正则"]),
           ("L2正则化", ["L2 regularization", "Ridge", "权重衰减", "weight decay"]),
           ("Dropout", ["dropout", "随机失活"]),
           ("数据增强", ["data augmentation"]),
           ("早停法", ["early stopping"]),
           ("交叉验证", ["cross validation", "CV"]),
           ("集成学习", ["ensemble learning"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("ml_004", "机器学习", 1,
       "解释一下 L1 和 L2 正则化的区别",
       "L1 正则化（Lasso）在损失函数中加入权重绝对值之和作为惩罚项，倾向于产生稀疏权重矩阵，即很多权重变为零，因此可以用于特征选择。L2 正则化（Ridge 或权重衰减）加入权重平方和作为惩罚项，倾向于让权重均匀变小但不为零，适合处理共线性特征。从几何角度看，L1 约束的等高线是菱形，优化解常落在角点（稀疏解）；L2 约束的等高线是圆形，优化解倾向于均匀分布。实际应用中，Elastic Net 结合了 L1 和 L2 的优点。在深度学习中，L2 正则化更常用，因为它不会过度稀疏化网络权重。",
       [
           ("L1正则化", ["L1 regularization", "Lasso", "L1正则"]),
           ("L2正则化", ["L2 regularization", "Ridge", "权重衰减", "weight decay"]),
           ("正则化", ["regularization"]),
           ("稀疏性", ["sparsity", "稀疏权重"]),
           ("特征选择", ["feature selection"]),
           ("Elastic Net", ["弹性网络", "elastic net"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("ml_005", "机器学习", 1,
       "交叉验证的原理和作用是什么？",
       "交叉验证是一种评估模型泛化能力的模型验证方法。最常用的是 K 折交叉验证：将数据集等分为 K 份，每次取其中 1 份作为验证集，其余 K-1 份作为训练集，重复 K 次，取 K 次验证结果的平均值作为最终评估指标。K 通常取 5 或 10。交叉验证的作用包括：更可靠地估计模型在未见数据上的表现，避免因单次划分导致的评估偏差；辅助超参数调优，通过比较不同超参数组合的交叉验证分数选择最优配置；以及评估模型稳定性。当数据量较小时，留一法交叉验证（LOOCV）是 K 折的极端情况，K 等于样本数。分层采样可保证每折的类别分布与整体一致。",
       [
           ("交叉验证", ["cross validation", "CV", "交叉检验"]),
           ("K折交叉验证", ["k-fold cross validation", "K折"]),
           ("验证集", ["validation set"]),
           ("训练集", ["training set"]),
           ("超参数调优", ["hyperparameter tuning", "超参数优化"]),
           ("留一法", ["leave-one-out cross validation", "LOOCV"]),
           ("分层采样", ["stratified sampling"]),
       ],
       tags=["cross_lang"]),

    qa("ml_006", "机器学习", 1,
       "随机森林是如何工作的？",
       "随机森林是一种基于决策树的集成学习方法。它通过 Bootstrap 采样从原始训练集中生成多个子集，每个子集训练一棵决策树。在树的每个节点分裂时，随机森林不从全部特征中选择最优分裂特征，而是从随机抽取的特征子集中选择，这进一步增加了树之间的差异性。预测时，分类问题采用多数投票，回归问题取平均值。随机森林的优点包括：能处理高维数据且不需要特征缩放，能评估特征重要性，对过拟合有较好的抵抗力（因为多棵树的平均降低了方差），以及并行训练效率高。袋外误差（OOB error）提供了一种无需额外验证集的内部评估方法。",
       [
           ("随机森林", ["random forest", "RF"]),
           ("决策树", ["decision tree"]),
           ("集成学习", ["ensemble learning", "模型集成"]),
           ("Bootstrap采样", ["bootstrap sampling", "自助采样法"]),
           ("特征重要性", ["feature importance"]),
           ("袋外误差", ["out-of-bag error", "OOB error"]),
           ("Bagging", ["bagging", "装袋法"]),
       ],
       tags=["cross_lang"]),

    qa("ml_007", "机器学习", 1,
       "支持向量机的基本原理是什么？",
       "支持向量机（SVM）是一种二分类模型，其基本思想是在特征空间中找到能最大化分类间隔的超平面。距离超平面最近的样本点称为支持向量，它们决定了超平面的位置。对于线性不可分的情况，SVM 引入软间隔允许一定程度的误分类，通过惩罚参数 C 控制间隔与误分类的权衡。对于非线性问题，SVM 使用核技巧将数据映射到高维空间使其线性可分，常用核函数包括径向基核函数（RBF）、多项式核和线性核。SVM 的对偶问题使其计算复杂度依赖支持向量数量而非特征维度，适合高维数据。SVM 也可推广到回归问题（SVR）和多分类。",
       [
           ("支持向量机", ["support vector machine", "SVM"]),
           ("支持向量", ["support vector"]),
           ("核技巧", ["kernel trick", "核方法"]),
           ("径向基核函数", ["radial basis function", "RBF", "高斯核"]),
           ("软间隔", ["soft margin"]),
           ("超平面", ["hyperplane"]),
           ("对偶问题", ["dual problem"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("ml_008", "机器学习", 1,
       "卷积神经网络的核心组件有哪些？",
       "卷积神经网络（CNN）是专门处理网格状数据（如图像）的深度学习模型。其核心组件包括：卷积层通过卷积核在输入上滑动做局部加权求和，提取空间特征，相比全连接层大幅减少参数量；池化层对特征图进行下采样，降低空间维度，最常用的是最大池化；激活函数通常使用 ReLU，提供非线性能力且计算高效；全连接层通常在网络的末端将特征映射到输出空间。CNN 的关键特性包括局部连接（每个神经元只关注局部区域）、权值共享（同一卷积核在整个输入上共享参数）和平移不变性。经典架构如 ResNet 引入残差连接解决深层网络退化问题，VGG 使用统一的小卷积核，Inception 模块则并行使用不同尺寸的卷积核。",
       [
           ("卷积神经网络", ["convolutional neural network", "CNN"]),
           ("卷积层", ["convolutional layer", "卷积"]),
           ("卷积核", ["convolution kernel", "filter", "滤波器"]),
           ("池化层", ["pooling layer", "池化"]),
           ("最大池化", ["max pooling"]),
           ("ReLU", ["rectified linear unit", "ReLU激活函数"]),
           ("全连接层", ["fully connected layer", "FC层", "dense layer"]),
           ("残差连接", ["residual connection", "skip connection", "跳跃连接"]),
           ("权值共享", ["weight sharing"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("ml_009", "机器学习", 1,
       "注意力机制的原理是什么？",
       "注意力机制借鉴人类视觉注意力的思想，让模型在处理输入时能动态地关注最相关的部分。在序列到序列任务中，注意力机制为解码器的每个时间步计算与编码器各位置的关联度（注意力分数），通常通过点积或加性方式计算，经 Softmax 归一化后作为权重对编码器输出加权求和，得到上下文向量。自注意力是 Transformer 的核心，它让序列中的每个位置都能直接关注所有其他位置，通过查询、键、值三组矩阵实现。多头注意力并行运行多个注意力头，使模型能同时关注不同子空间的信息。注意力机制解决了 RNN 长序列依赖和并行化困难的问题。",
       [
           ("注意力机制", ["attention mechanism", "attention"]),
           ("自注意力", ["self-attention", "自注意力机制"]),
           ("Transformer", ["transformer"]),
           ("多头注意力", ["multi-head attention", "MHA"]),
           ("Softmax", ["softmax", "归一化指数函数"]),
           ("上下文向量", ["context vector"]),
           ("查询键值", ["query key value", "QKV"]),
           ("循环神经网络", ["recurrent neural network", "RNN"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("ml_010", "机器学习", 1,
       "批归一化的作用和原理是什么？",
       "批归一化（Batch Normalization, BN）通过在每个 mini-batch 内对层的输入进行标准化，将其归一化到均值为 0、方差为 1 的分布，然后通过可学习的缩放和平移参数恢复表达能力。BN 的主要作用包括：加速训练收敛，允许使用更大的学习率；缓解内部协变量偏移问题，即深层网络中各层输入分布随训练变化的问题；降低对参数初始化的敏感性；以及具有一定的正则化效果。BN 在推理时使用训练阶段累积的移动平均值而非 batch 统计量。BN 的变体包括层归一化（Layer Norm，对单个样本的所有特征归一化，适合序列模型）和组归一化（Group Norm，将通道分组归一化，适合小 batch 场景）。",
       [
           ("批归一化", ["batch normalization", "BatchNorm", "BN"]),
           ("内部协变量偏移", ["internal covariate shift"]),
           ("层归一化", ["layer normalization", "LayerNorm", "LN"]),
           ("组归一化", ["group normalization", "GroupNorm", "GN"]),
           ("学习率", ["learning rate"]),
           ("正则化", ["regularization"]),
           ("移动平均", ["moving average", "滑动平均"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("ml_011", "机器学习", 1,
       "常见的损失函数有哪些？",
       "损失函数衡量模型预测值与真实值之间的差异，是模型优化的目标。回归任务常用均方误差（MSE），对大误差敏感；平均绝对误差（MAE）对异常值更鲁棒。分类任务常用交叉熵损失，二分类用二元交叉熵，多分类用分类交叉熵，它能度量预测概率分布与真实分布的差异。Focal Loss 在交叉熵基础上增加调制因子，解决类别不平衡问题。排序任务常用 Hinge Loss 或对比损失。在深度学习中，损失函数的选择应与任务对齐，有时需要组合多种损失。例如目标检测同时使用分类损失和边界框回归损失。损失函数的设计直接影响模型优化的方向和效果。",
       [
           ("损失函数", ["loss function", "代价函数", "cost function"]),
           ("均方误差", ["mean squared error", "MSE", "L2损失"]),
           ("平均绝对误差", ["mean absolute error", "MAE", "L1损失"]),
           ("交叉熵", ["cross entropy", "交叉熵损失"]),
           ("二元交叉熵", ["binary cross entropy", "BCE"]),
           ("Focal Loss", ["focal loss"]),
           ("Hinge Loss", ["hinge loss", "合页损失"]),
           ("类别不平衡", ["class imbalance", "类别不均衡"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("ml_012", "机器学习", 1,
       "集成学习的主要方法有哪些？",
       "集成学习通过组合多个基学习器来提升预测性能，主要分为两类。Bagging（Bootstrap Aggregating）对训练数据进行有放回采样生成多个子集，独立训练多个基学习器后通过投票或平均集成，代表算法是随机森林，主要降低方差。Boosting 是序列化方法，每个新模型着重纠正前一个模型的错误，通过调整样本权重使后续模型关注难分类样本，代表算法包括 AdaBoost、梯度提升树（GBDT）和 XGBoost、LightGBM，主要降低偏差。Stacking 用初级学习器的预测作为次级学习器的输入进行再训练。集成学习的效果取决于基学习器的准确性和多样性，基学习器之间差异越大，集成效果通常越好。",
       [
           ("集成学习", ["ensemble learning", "模型集成"]),
           ("Bagging", ["bagging", "装袋法", "Bootstrap Aggregating"]),
           ("Boosting", ["boosting", "提升法"]),
           ("随机森林", ["random forest", "RF"]),
           ("AdaBoost", ["adaptive boosting", "AdaBoost"]),
           ("梯度提升树", ["gradient boosting decision tree", "GBDT", "梯度提升"]),
           ("XGBoost", ["extreme gradient boosting"]),
           ("LightGBM", ["light gradient boosting machine"]),
           ("Stacking", ["stacking", "堆叠法"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("ml_013", "机器学习", 1,
       "主成分分析的原理和应用场景",
       "主成分分析（PCA）是一种无监督的线性降维方法。它通过协方差矩阵的特征值分解找到数据方差最大的方向，将高维数据投影到低维空间同时保留尽可能多的信息。具体步骤包括：数据中心化，计算协方差矩阵，求特征值和特征向量，按特征值大小排序选取前 K 个特征向量作为主成分，将原始数据投影到这 K 个主成分构成的子空间。PCA 的应用场景包括数据可视化（降至 2-3 维）、特征压缩、噪声过滤和加速后续模型训练。PCA 的局限性在于它只捕获线性关系，且主成分可能缺乏可解释性。对于非线性降维，可使用 t-SNE 或 UMAP。选择主成分数量通常通过累计方差贡献率确定，一般保留 95% 以上的方差。",
       [
           ("主成分分析", ["principal component analysis", "PCA"]),
           ("降维", ["dimensionality reduction", "维度约简"]),
           ("协方差矩阵", ["covariance matrix"]),
           ("特征值分解", ["eigenvalue decomposition", "特征分解"]),
           ("特征向量", ["eigenvector"]),
           ("方差贡献率", ["variance explained", "explained variance ratio"]),
           ("t-SNE", ["t-SNE", "t分布随机邻域嵌入"]),
           ("UMAP", ["uniform manifold approximation and projection"]),
       ],
       tags=["cross_lang"]),

    qa("ml_014", "机器学习", 1,
       "K均值聚类的工作原理",
       "K均值聚类是一种经典的无监督聚类算法。算法流程为：随机初始化 K 个聚类中心，然后迭代执行两个步骤——分配步骤将每个样本分配到最近的聚类中心所在的簇，更新步骤将每个簇的中心设为该簇所有样本的均值，直到聚类中心不再显著变化或达到最大迭代次数。K 值的选择可通过肘部法则或轮廓系数确定。K均值的优点是简单高效、可扩展性好；缺点是对初始中心敏感（可用 K-means++ 改进）、只能发现球形簇、对异常值敏感、需预先指定 K 值。对于非凸形状的簇，可使用 DBSCAN 等基于密度的方法。K均值常用于客户分群、图像压缩、异常检测等场景。",
       [
           ("K均值聚类", ["k-means clustering", "K-means", "K均值"]),
           ("聚类中心", ["cluster center", "质心", "centroid"]),
           ("肘部法则", ["elbow method"]),
           ("轮廓系数", ["silhouette coefficient", "silhouette score"]),
           ("K-means++", ["k-means++", "K-means plus"]),
           ("DBSCAN", ["density-based spatial clustering", "DBSCAN"]),
           ("无监督学习", ["unsupervised learning"]),
       ],
       tags=["cross_lang"]),

    # ── depth 3 ──
    qa("ml_015", "机器学习", 3,
       "学习率衰减策略有哪些？",
       "学习率衰减是在训练过程中逐步降低学习率的策略，前期用较大学习率快速接近最优区域，后期用较小学习率精细调整。常见策略包括：阶跃衰减按固定轮数将学习率乘以衰减因子；余弦退火按余弦函数周期性变化学习率，可配合热重启；指数衰减按指数函数连续降低；多项式衰减在指定轮数内平滑降至最小值。Warmup 策略在训练初期从零线性增大学习率，避免初始阶段梯度不稳定，在 Transformer 训练中广泛使用。自适应学习率方法如 Adam、AdamW 内置了逐参数的自适应调整，但仍可与全局衰减策略组合使用。学习率衰减的有效性依赖于合理的初始学习率和总训练轮数设置。",
       [
           ("学习率衰减", ["learning rate decay", "学习率调度", "lr schedule"]),
           ("余弦退火", ["cosine annealing", "余弦退火学习率"]),
           ("热重启", ["warm restart", "SGDR"]),
           ("Warmup", ["learning rate warmup", "预热"]),
           ("Adam", ["adaptive moment estimation", "Adam优化器"]),
           ("AdamW", ["AdamW", "decoupled weight decay"]),
           ("阶跃衰减", ["step decay"]),
       ],
       chain=["神经网络", "优化算法", "学习率"],
       tags=["cross_lang", "depth_sensitive"]),

    qa("ml_016", "机器学习", 3,
       "梯度消失和梯度爆炸问题的成因和解决方案",
       "梯度消失和梯度爆炸是深度神经网络训练中的常见问题，根源在于反向传播时梯度通过链式法则逐层相乘。当激活函数导数或权重值小于 1 时，梯度在多层传播后会指数级衰减（梯度消失），导致浅层权重几乎不更新；当权重值大于 1 时则指数级增长（梯度爆炸），导致参数震荡发散。解决方案包括：使用 ReLU 等非饱和激活函数避免导数过小；残差连接提供梯度捷径绕过深层乘法；批归一化和层归一化控制每层输入分布；梯度裁剪限制梯度范数防止爆炸；合理的权重初始化（如 He 初始化、Xavier 初始化）使各层方差稳定；以及使用 LSTM 或 GRU 缓解循环网络中的长程依赖问题。",
       [
           ("梯度消失", ["vanishing gradient", "梯度消失问题"]),
           ("梯度爆炸", ["exploding gradient", "梯度爆炸问题"]),
           ("残差连接", ["residual connection", "skip connection", "跳跃连接"]),
           ("梯度裁剪", ["gradient clipping"]),
           ("ReLU", ["rectified linear unit"]),
           ("批归一化", ["batch normalization", "BatchNorm", "BN"]),
           ("He初始化", ["He initialization", "He正态初始化"]),
           ("Xavier初始化", ["Xavier initialization", "Glorot初始化"]),
           ("LSTM", ["long short-term memory", "长短期记忆网络"]),
       ],
       chain=["深度学习", "反向传播", "梯度消失"],
       tags=["alias_heavy", "cross_lang", "depth_sensitive"]),

    # ── depth 4 ──
    qa("ml_017", "机器学习", 4,
       "Dropout 的具体实现和原理",
       "Dropout 是一种深度学习正则化技术，在训练时以概率 p 随机将神经元输出置零，被丢弃的神经元不参与本轮前向传播和反向传播。推理时不执行丢弃，但需将输出乘以 (1-p) 以保持期望值一致（或训练时做反向缩放）。Dropout 的正则化机制包括：阻止神经元共适应，迫使每个神经元学习更鲁棒的特征；等效于训练 exponentially 级别的子网络的集成；在权重空间中引入噪声起到正则化效果。Dropout 率 p 通常设为 0.2-0.5，过大会导致欠拟合。Dropout 常用于全连接层，卷积层中由于特征图空间相关性强，效果有限，可改用 Spatial Dropout 按通道丢弃。Dropout 可与批归一化组合使用，但需注意训练/推理模式的切换顺序。",
       [
           ("Dropout", ["dropout", "随机失活"]),
           ("正则化", ["regularization"]),
           ("共适应", ["co-adaptation", "神经元共适应"]),
           ("Spatial Dropout", ["spatial dropout", "空间随机失活"]),
           ("批归一化", ["batch normalization", "BatchNorm", "BN"]),
           ("欠拟合", ["underfitting"]),
           ("集成学习", ["ensemble learning"]),
       ],
       chain=["机器学习", "正则化", "深度学习正则化", "Dropout"],
       tags=["cross_lang", "depth_sensitive"]),

    qa("ml_018", "机器学习", 4,
       "Transformer 的位置编码为什么重要？",
       "Transformer 完全基于自注意力机制，没有循环或卷积结构，本身不具备捕捉序列顺序的能力。位置编码为模型注入位置信息，使注意力机制能区分不同位置的 token。原始 Transformer 使用正弦余弦函数生成固定的位置编码，不同维度使用不同频率的正弦波，使模型能学习相对位置关系。可学习的位置编码将位置嵌入作为可训练参数，灵活性更强但缺乏外推能力。旋转位置编码（RoPE）通过旋转矩阵将相对位置信息编码到注意力计算中，支持长度外推，在 LLaMA 等大模型中广泛应用。ALiBi 则通过在注意力分数中加上与距离成比例的偏置实现位置感知。位置编码的设计直接影响模型对长序列的建模能力。",
       [
           ("位置编码", ["positional encoding", "位置嵌入", "positional embedding"]),
           ("Transformer", ["transformer"]),
           ("自注意力", ["self-attention"]),
           ("旋转位置编码", ["rotary position embedding", "RoPE"]),
           ("正弦余弦编码", ["sinusoidal positional encoding", "正弦位置编码"]),
           ("长度外推", ["length extrapolation", "外推性"]),
           ("ALiBi", ["attention with linear biases", "ALiBi"]),
       ],
       chain=["深度学习", "注意力机制", "序列模型", "Transformer"],
       tags=["cross_lang", "depth_sensitive"]),

    # ── depth 5 ──
    qa("ml_019", "机器学习", 5,
       "残差连接为什么能解决深层网络退化？",
       "残差连接（Residual Connection）通过将输入直接加到输出上，形成恒等映射的捷径。其核心洞察是：深层网络至少不应比浅层网络差，但实际训练中深层网络出现退化（非过拟合），说明优化困难。残差连接将网络的学习目标从拟合完整映射 H(x) 改为拟合残差 F(x) = H(x) - x，当最优解接近恒等映射时，将残差驱动到零比学习恒等映射更容易。从梯度流角度，残差连接提供了梯度直接传播的捷径，使梯度可以绕过深层非线性变换，缓解梯度消失。数学上，残差块的梯度为 dF/dx + 1，常数项 1 保证梯度至少不衰减。残差连接使得训练上百甚至上千层的网络成为可能，是 ResNet 成功的关键。",
       [
           ("残差连接", ["residual connection", "skip connection", "跳跃连接"]),
           ("恒等映射", ["identity mapping", "恒等映射"]),
           ("梯度消失", ["vanishing gradient"]),
           ("ResNet", ["residual network", "残差网络"]),
           ("网络退化", ["degradation problem", "退化问题"]),
           ("梯度流", ["gradient flow", "梯度传播"]),
       ],
       chain=["深度学习", "卷积神经网络", "深层网络训练", "梯度问题", "残差连接"],
       tags=["cross_lang", "depth_sensitive"]),

    qa("ml_020", "机器学习", 5,
       "对比学习中 InfoNCE 损失的作用",
       "InfoNCE 损失是对比学习中的核心目标函数，源于噪声对比估计（NCE）的信息论推广。它通过 InfoNCE 将正样本对拉近、负样本对推远来学习表征。具体形式为：给定一个查询样本和一组候选样本（含 1 个正样本和 N-1 个负样本），InfoNCE 损失等价于让模型从 N 个候选中识别出正样本的分类交叉熵。温度系数控制分布的平滑程度，较小的温度使模型更关注困难负样本。InfoNCE 的下界与互信息相关，最大化正样本对之间的互信息下界。在 SimCLR 中，InfoNCE 对同一图像的两个增强视图（正对）与不同图像的视图（负对）计算。MoCo 通过动量编码器和队列维护大量负样本。InfoNCE 的有效性依赖于足够多的负样本和有效的数据增强策略。",
       [
           ("对比学习", ["contrastive learning"]),
           ("InfoNCE", ["info noise contrastive estimation", "InfoNCE损失"]),
           ("噪声对比估计", ["noise contrastive estimation", "NCE"]),
           ("互信息", ["mutual information"]),
           ("SimCLR", ["simple framework for contrastive learning"]),
           ("MoCo", ["momentum contrast"]),
           ("温度系数", ["temperature parameter", "温度参数"]),
           ("数据增强", ["data augmentation"]),
           ("动量编码器", ["momentum encoder"]),
       ],
       chain=["机器学习", "表征学习", "自监督学习", "对比学习", "InfoNCE损失"],
       tags=["cross_lang", "depth_sensitive"]),
]


# ══════════════════════════════════════════════════════
# 领域 2: 计算机网络
# ══════════════════════════════════════════════════════

NET_QA = [
    qa("net_001", "计算机网络", 1,
       "TCP 三次握手的过程是什么？",
       "TCP 三次握手是建立 TCP 连接的标准过程。第一次握手：客户端发送 SYN 报文（SYN=1, seq=x）给服务器，进入 SYN_SENT 状态，表示请求建立连接。第二次握手：服务器收到 SYN 后，回复 SYN+ACK 报文（SYN=1, ACK=1, seq=y, ack=x+1），进入 SYN_RCVD 状态，表示同意连接并确认客户端的序列号。第三次握手：客户端收到 SYN+ACK 后，发送 ACK 报文（ACK=1, seq=x+1, ack=y+1），双方进入 ESTABLISHED 状态，连接建立完成。三次握手的目的包括：确认双方的收发能力正常，协商初始序列号（ISN）以防止旧连接的报文干扰，以及防止已失效的连接请求报文突然到达服务器造成资源浪费。",
       [
           ("TCP三次握手", ["TCP three-way handshake", "三次握手", "TCP握手"]),
           ("SYN", ["synchronize", "SYN报文", "同步报文"]),
           ("ACK", ["acknowledgment", "确认报文", "ACK报文"]),
           ("初始序列号", ["initial sequence number", "ISN"]),
           ("连接状态", ["connection state", "TCP状态"]),
           ("ESTABLISHED", ["established", "已建立连接"]),
       ],
       tags=["cross_lang"]),

    qa("net_002", "计算机网络", 1,
       "HTTP 和 HTTPS 的区别是什么？",
       "HTTP（超文本传输协议）和 HTTPS（HTTP Secure）的主要区别在于安全性。HTTP 明文传输数据，数据在传输过程中可被窃听、篡改和伪造。HTTPS 在 HTTP 和 TCP 之间加入了 SSL/TLS 层，通过加密、身份认证和数据完整性保护来安全传输。HTTPS 使用非对称加密交换会话密钥，使用对称加密传输数据，兼顾安全性和性能。HTTPS 需要 CA 证书验证服务器身份，防止中间人攻击。HTTPS 默认端口 443，HTTP 默认端口 80。HTTPS 会增加少量延迟（TLS 握手开销），但 HTTP/2 和 TLS 1.3 的优化已大幅减少这一开销。现代浏览器对 HTTP 站点标记为不安全，推动 HTTPS 成为默认标准。",
       [
           ("HTTP", ["hypertext transfer protocol", "超文本传输协议"]),
           ("HTTPS", ["HTTP secure", "HTTP over TLS"]),
           ("SSL/TLS", ["secure sockets layer", "transport layer security", "SSL", "TLS"]),
           ("非对称加密", ["asymmetric encryption", "公钥加密"]),
           ("对称加密", ["symmetric encryption"]),
           ("CA证书", ["certificate authority", "CA", "数字证书"]),
           ("中间人攻击", ["man-in-the-middle attack", "MITM"]),
           ("HTTP/2", ["HTTP 2.0", "H2"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("net_003", "计算机网络", 1,
       "DNS 解析的完整流程",
       "DNS 解析是将域名转换为 IP 地址的过程。当用户在浏览器输入域名后，系统首先检查本地 DNS 缓存，未命中则向本地域名服务器（通常由 ISP 提供的递归解析服务器）发起查询。递归解析服务器依次查询：根域名服务器（返回顶级域服务器地址）、顶级域名服务器如 .com 服务器（返回权威域名服务器地址）、权威域名服务器（返回域名的最终记录）。解析结果逐级返回并缓存，缓存时间由 TTL 决定。DNS 记录类型包括 A 记录（域名到 IPv4）、AAAA 记录（域名到 IPv6）、CNAME 记录（域名别名）、MX 记录（邮件交换）等。DNS 基于 UDP 协议，端口 53，大响应会切换到 TCP。DNSSEC 为 DNS 提供来源验证和完整性保护。",
       [
           ("DNS", ["domain name system", "域名系统", "域名解析"]),
           ("递归解析", ["recursive resolution", "递归查询"]),
           ("根域名服务器", ["root name server", "根服务器"]),
           ("权威域名服务器", ["authoritative name server", "权威服务器"]),
           ("TTL", ["time to live", "生存时间"]),
           ("A记录", ["A record", "地址记录"]),
           ("CNAME", ["canonical name record", "CNAME记录", "别名记录"]),
           ("DNSSEC", ["DNS security extensions"]),
       ],
       tags=["cross_lang"]),

    qa("net_004", "计算机网络", 1,
       "CDN 的工作原理是什么？",
       "CDN（内容分发网络）通过在全球部署的边缘节点缓存源站内容，使用户就近获取数据，从而降低延迟、减轻源站负载。工作流程为：用户请求域名时，DNS 解析将请求路由到离用户最近的 CDN 边缘节点；边缘节点检查缓存是否命中，命中则直接返回内容，未命中则向源站回源获取内容并缓存后返回。CDN 的智能调度基于 DNS 调度、Anycast 或 HTTP 302 重定向实现。CDN 的核心价值包括：加速静态资源（图片、CSS、JS）和动态内容加速（通过路由优化和安全隧道）；提供 DDoS 防护和 WAF 安全能力；支持视频流媒体分发和直播加速。缓存刷新和预热是 CDN 运维的关键操作。",
       [
           ("CDN", ["content delivery network", "内容分发网络"]),
           ("边缘节点", ["edge node", "边缘服务器", "CDN节点"]),
           ("缓存命中", ["cache hit"]),
           ("回源", ["origin fetch", "回源请求"]),
           ("Anycast", ["anycast", "任播"]),
           ("DDoS防护", ["DDoS protection", "分布式拒绝服务防护"]),
           ("WAF", ["web application firewall", "Web应用防火墙"]),
           ("缓存预热", ["cache preheating", "缓存预热"]),
       ],
       tags=["cross_lang"]),

    qa("net_005", "计算机网络", 1,
       "负载均衡的常见算法和策略",
       "负载均衡将流量分发到多台后端服务器，提高系统可用性和吞吐量。常见算法包括：轮询按顺序依次分配，简单但不考虑服务器差异；加权轮询根据服务器性能分配不同权重；最少连接将请求分发给当前连接数最少的服务器；IP 哈希使同一客户端的请求固定到同一服务器（会话保持）。负载均衡可在不同层级实现：L4 负载均衡（如 LVS）基于 IP 和端口转发，性能高但不感知应用层；L7 负载均衡（如 Nginx、HAProxy）基于 HTTP 头、URL 等应用层信息路由，更灵活。健康检查定期探测后端服务器状态，自动剔除故障节点。负载均衡器本身也需高可用，通常通过主备或双活模式部署。",
       [
           ("负载均衡", ["load balancing", "LB"]),
           ("轮询", ["round robin", "轮询算法"]),
           ("加权轮询", ["weighted round robin", "WRR"]),
           ("最少连接", ["least connections", "最小连接数"]),
           ("IP哈希", ["ip hash", "IP散列"]),
           ("L4负载均衡", ["layer 4 load balancing", "四层负载均衡"]),
           ("L7负载均衡", ["layer 7 load balancing", "七层负载均衡"]),
           ("LVS", ["linux virtual server"]),
           ("健康检查", ["health check", "健康探测"]),
       ],
       tags=["cross_lang"]),

    qa("net_006", "计算机网络", 1,
       "SSL/TLS 握手过程详解",
       "SSL/TLS 握手建立安全通信所需的加密参数。以 TLS 1.2 为例：客户端发送 ClientHello，包含支持的 TLS 版本、加密套件列表和客户端随机数。服务器回复 ServerHello，选定加密套件和服务器随机数，并发送证书供客户端验证身份。如果使用 ECDHE 密钥交换，服务器额外发送 Server Key Exchange 和 ServerHello Done。客户端验证证书后，发送 Client Key Exchange 完成密钥交换。双方用三个随机数生成会话密钥，互发 Change Cipher Spec 和 Finished 消息确认握手完成。TLS 1.3 简化握手为 1-RTT（甚至 0-RTT），移除了不安全的加密算法，提升了安全性和性能。会话恢复通过 Session ID 或 Session Ticket 减少后续连接的握手开销。",
       [
           ("SSL/TLS", ["SSL", "TLS", "transport layer security"]),
           ("TLS握手", ["TLS handshake", "SSL握手"]),
           ("加密套件", ["cipher suite", "密码套件"]),
           ("数字证书", ["digital certificate", "X.509证书"]),
           ("ECDHE", ["elliptic curve diffie-hellman ephemeral", "ECDHE"]),
           ("会话密钥", ["session key"]),
           ("Session Ticket", ["session ticket", "会话票据"]),
           ("TLS 1.3", ["TLS 1.3"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("net_007", "计算机网络", 1,
       "WebSocket 与 HTTP 的区别",
       "WebSocket 是一种在单个 TCP 连接上进行全双工通信的协议。与 HTTP 的区别在于：HTTP 是请求-响应模式，每次通信需新建连接或复用 Keep-Alive 连接，服务器无法主动推送；WebSocket 建立连接后保持持久连接，服务器可随时主动推送数据，适合实时场景。WebSocket 通过 HTTP Upgrade 头完成协议升级握手，之后通信切换为 WebSocket 帧格式。WebSocket 的优势包括：低延迟（无需重复握手）、低开销（帧头仅 2-10 字节）、双向通信。典型应用包括即时通讯、实时协作、股票行情和在线游戏。当 WebSocket 不可用时，可降级为 SSE（Server-Sent Events）或 HTTP 长轮询。",
       [
           ("WebSocket", ["websocket", "WS"]),
           ("全双工通信", ["full-duplex communication", "双向通信"]),
           ("HTTP长轮询", ["long polling", "HTTP长轮询"]),
           ("SSE", ["server-sent events", "服务器发送事件"]),
           ("协议升级", ["protocol upgrade", "HTTP Upgrade"]),
           ("Keep-Alive", ["keep-alive", "HTTP持久连接"]),
           ("TCP连接", ["TCP connection"]),
       ],
       tags=["cross_lang"]),

    qa("net_008", "计算机网络", 1,
       "IPv4 和 IPv6 的主要区别",
       "IPv4 和 IPv6 是互联网协议的两个版本。IPv4 地址长度 32 位，约 43 亿个地址，已接近耗尽；IPv6 地址长度 128 位，提供近乎无限的地址空间。IPv4 地址用点分十进制表示（如 192.168.1.1），IPv6 用冒号十六进制表示（如 2001:db8::1）。IPv6 简化了头部格式，移除了校验和字段，提高了路由效率。IPv6 内置 IPSec 安全支持，而 IPv4 的 IPSec 是可选扩展。IPv6 取消了广播，使用多播和任播替代。NAT 在 IPv4 中广泛用于缓解地址不足，IPv6 的地址空间使每台设备可拥有全球唯一地址，理论上无需 NAT。IPv4 和 IPv6 不直接互通，需要双栈或隧道技术过渡。",
       [
           ("IPv4", ["internet protocol version 4", "IPv4"]),
           ("IPv6", ["internet protocol version 6", "IPv6"]),
           ("NAT", ["network address translation", "网络地址转换"]),
           ("IPSec", ["internet protocol security", "IPSec"]),
           ("多播", ["multicast", "组播"]),
           ("双栈", ["dual stack", "双协议栈"]),
           ("隧道技术", ["tunneling", "隧道"]),
       ],
       tags=["cross_lang"]),

    qa("net_009", "计算机网络", 1,
       "TCP 拥塞控制算法",
       "TCP 拥塞控制防止网络因过载而崩溃，核心是拥塞窗口（cwnd）的动态调整。慢启动阶段，cwnd 从 1 指数增长到慢启动阈值（ssthresh）。拥塞避免阶段，cwnd 线性增长（每 RTT 加 1），温和探测可用带宽。发现丢包时，根据机制不同处理：Tahoe/Reno 通过超时或重复 ACK 判断丢包；Reno 对三次重复 ACK 执行快重传和快恢复（cwnd 减半），对超时则重置 cwnd 到 1。CUBIC 是 Linux 默认算法，用三次函数窗口增长曲线提高高带宽延迟积网络下的吞吐量。BBR 由 Google 提出，基于带宽和 RTT 估计而非丢包驱动，更适合现代浅缓冲网络。拥塞控制与流量控制不同，后者通过滑动窗口保护接收方。",
       [
           ("拥塞控制", ["congestion control", "TCP拥塞控制"]),
           ("拥塞窗口", ["congestion window", "cwnd"]),
           ("慢启动", ["slow start"]),
           ("拥塞避免", ["congestion avoidance"]),
           ("快重传", ["fast retransmit"]),
           ("快恢复", ["fast recovery"]),
           ("CUBIC", ["CUBIC", "TCP CUBIC"]),
           ("BBR", ["bottleneck bandwidth and round-trip propagation time", "BBR"]),
           ("流量控制", ["flow control", "滑动窗口"]),
       ],
       tags=["cross_lang"]),

    qa("net_010", "计算机网络", 1,
       "RESTful API 的设计原则",
       "REST（表述性状态转移）是一种 Web API 架构风格。核心原则包括：资源导向，每个资源有唯一 URI 标识；统一接口，使用 HTTP 方法表达操作语义——GET 查询、POST 创建、PUT 更新（整体）、PATCH 更新（部分）、DELETE 删除；无状态，每个请求包含所有必要信息，服务器不保存客户端会话状态；分层架构，客户端不感知中间代理。RESTful API 使用 HTTP 状态码表达结果（200 成功、201 创建、400 客户端错误、404 未找到、500 服务器错误）。版本控制通过 URI 路径或请求头实现。HATEOAS 原则要求响应中包含相关资源的链接，使客户端能动态发现 API 能力。REST 的优势在于简单、可缓存、可扩展，适合 CRUD 场景。",
       [
           ("REST", ["representational state transfer", "RESTful"]),
           ("RESTful API", ["REST API", "RESTful接口"]),
           ("资源", ["resource"]),
           ("HTTP方法", ["HTTP methods", "HTTP动词"]),
           ("无状态", ["stateless", "无状态性"]),
           ("HATEOAS", ["hypermedia as the engine of application state"]),
           ("URI", ["uniform resource identifier", "统一资源标识符"]),
           ("HTTP状态码", ["HTTP status code", "HTTP状态码"]),
       ],
       tags=["cross_lang"]),

    qa("net_011", "计算机网络", 1,
       "gRPC 与 REST 的对比",
       "gRPC 是 Google 开源的高性能 RPC 框架，基于 HTTP/2 和 Protocol Buffers。与 REST 相比：gRPC 使用二进制编码的 Protobuf，序列化体积小、速度快，REST 通常用 JSON 文本格式。gRPC 基于 HTTP/2 多路复用，单个连接可并发多个请求，REST 通常基于 HTTP/1.1 需多个连接。gRPC 原生支持流式传输（服务端流、客户端流、双向流），REST 需借助 SSE 或 WebSocket。gRPC 严格类型安全，通过 proto 文件定义接口契约并自动生成多语言客户端代码，REST 的 OpenAPI 规范相对松散。gRPC 的缺点是浏览器支持不友好（需 gRPC-Web 代理），调试不如 REST 直观。gRPC 适合微服务内部通信，REST 更适合面向前端的公开 API。",
       [
           ("gRPC", ["gRPC", "google RPC"]),
           ("REST", ["representational state transfer", "RESTful"]),
           ("Protocol Buffers", ["protobuf", "Protocol Buffers", "Protobuf"]),
           ("HTTP/2", ["HTTP 2.0", "H2"]),
           ("多路复用", ["multiplexing", "HTTP/2多路复用"]),
           ("流式传输", ["streaming", "gRPC streaming"]),
           ("序列化", ["serialization", "序列化"]),
           ("微服务", ["microservices", "微服务架构"]),
       ],
       tags=["cross_lang"]),

    qa("net_012", "计算机网络", 1,
       "NAT 的工作原理和类型",
       "NAT（网络地址转换）在路由器上修改 IP 报文的源或目的地址，实现私有 IP 与公网 IP 之间的转换。NAT 的主要目的是缓解 IPv4 地址不足，同时提供一定的网络隔离。NAT 维护一张转换表，记录私有地址:端口到公网地址:端口的映射。NAT 类型包括：静态 NAT 一对一映射；动态 NAT 从地址池中动态分配；NAPT（端口地址转换 / PAT）多对一映射，通过不同端口区分连接，是最常见的类型。NAT 的副作用包括：破坏了端到端原则，某些协议（如 FTP、SIP）需要 ALG 辅助穿越；影响 P2P 连接建立，需要 STUN/TURN/ICE 等穿透技术。NAT 穿越在 WebRTC 中是关键挑战。",
       [
           ("NAT", ["network address translation", "网络地址转换"]),
           ("NAPT", ["network address port translation", "PAT", "端口地址转换"]),
           ("NAT穿透", ["NAT traversal", "NAT穿透"]),
           ("STUN", ["session traversal utilities for NAT"]),
           ("TURN", ["traversal using relays around NAT"]),
           ("ICE", ["interactive connectivity establishment"]),
           ("WebRTC", ["WebRTC", "Web实时通信"]),
           ("ALG", ["application layer gateway", "应用层网关"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("net_013", "计算机网络", 1,
       "代理服务器（正向代理与反向代理）",
       "正向代理和反向代理都位于客户端和服务器之间，但代理的对象不同。正向代理代理客户端，客户端知道目标服务器但通过代理发送请求，用途包括突破访问限制、隐藏客户端 IP、缓存加速和访问控制。反向代理代理服务器，客户端不知道真实服务器，以为反向代理就是目标，用途包括负载均衡、SSL 卸载、缓存、安全防护和统一入口。Nginx 是最常用的反向代理服务器，Squid、Varnish 常用作缓存代理。CDN 本质上是分布式的反向代理。透明代理不修改请求和响应，仅做转发和监控，常用于企业网络管控。代理服务器的选择取决于使用场景：正向代理侧重客户端隐私和访问控制，反向代理侧重服务端性能和安全。",
       [
           ("正向代理", ["forward proxy", "前向代理"]),
           ("反向代理", ["reverse proxy"]),
           ("Nginx", ["nginx", "Nginx服务器"]),
           ("SSL卸载", ["SSL offloading", "TLS终止"]),
           ("CDN", ["content delivery network", "内容分发网络"]),
           ("Varnish", ["Varnish", "Varnish缓存"]),
           ("透明代理", ["transparent proxy"]),
       ],
       tags=["cross_lang"]),

    qa("net_014", "计算机网络", 1,
       "OSPF 路由协议的工作原理",
       "OSPF（开放最短路径优先）是一种链路状态路由协议，基于 Dijkstra 最短路径算法计算路由。每台 OSPF 路由器维护一个链路状态数据库（LSDB），描述整个网络的拓扑结构。路由器通过 Hello 报文发现邻居并建立邻接关系，通过 LSA（链路状态通告）向全网泛洪自己的链路状态信息。所有路由器的 LSDB 同步后，各自以自己为根运行 Dijkstra 算法计算到每个目的地的最短路径树。OSPF 支持 AREA 分层架构，骨干区域（Area 0）连接所有非骨干区域，减少 LSA 泛洪范围和路由表规模。OSPF 的优势包括：收敛速度快、支持等价多路径（ECMP）、无跳数限制、支持 VLSM。OSPF 的开销基于带宽计算，优先选择高带宽路径。",
       [
           ("OSPF", ["open shortest path first", "OSPF协议"]),
           ("链路状态协议", ["link state protocol", "链路状态路由协议"]),
           ("Dijkstra算法", ["Dijkstra algorithm", "最短路径算法"]),
           ("链路状态数据库", ["link state database", "LSDB"]),
           ("LSA", ["link state advertisement", "链路状态通告"]),
           ("区域", ["area", "OSPF区域"]),
           ("ECMP", ["equal cost multi-path", "等价多路径"]),
           ("VLSM", ["variable length subnet masking", "可变长子网掩码"]),
       ],
       tags=["cross_lang"]),

    # ── depth 3 ──
    qa("net_015", "计算机网络", 3,
       "TLS 1.3 的 0-RTT 模式如何工作？",
       "TLS 1.3 的 0-RTT（零往返时间）模式允许客户端在握手第一个报文中就携带应用数据，实现连接恢复时的零延迟。工作原理：首次完整握手时，服务器通过 New Session Ticket 向客户端分发预共享密钥（PSK）。后续连接中，客户端在 ClientHello 中携带 Early Data 和 PSK 标识，同时用 PSK 派生的密钥加密应用数据一并发送。服务器验证 PSK 后即可立即处理 Early Data，无需等待握手完成。0-RTT 的安全限制：Early Data 只能用于幂等请求（如 GET），因为 0-RTT 数据可能被重放攻击；0-RTT 不提供前向安全性，PSK 泄露会暴露 0-RTT 数据。0-RTT 的防重放机制包括服务器端记录已使用的 PSK 或使用单次票据。",
       [
           ("0-RTT", ["zero round trip time", "0-RTT", "零往返时间"]),
           ("TLS 1.3", ["TLS 1.3"]),
           ("预共享密钥", ["pre-shared key", "PSK"]),
           ("Early Data", ["early data", "0-RTT数据"]),
           ("重放攻击", ["replay attack", "重放"]),
           ("前向安全", ["forward secrecy", "前向保密", "PFS"]),
           ("New Session Ticket", ["new session ticket", "会话票据"]),
           ("幂等", ["idempotent", "幂等性"]),
       ],
       chain=["计算机网络", "安全协议", "TLS 1.3"],
       tags=["cross_lang", "depth_sensitive"]),

    qa("net_016", "计算机网络", 3,
       "BBR 拥塞控制算法的核心思想",
       "BBR（Bottleneck Bandwidth and RTT）是 Google 提出的基于模型的拥塞控制算法。与传统基于丢包的算法（CUBIC、Reno）不同，BBR 不将丢包视为拥塞信号，而是主动估计瓶颈带宽（BtlBw）和最小 RTT（RTprop）两个关键参数。BBR 的状态机包含四个阶段：Startup 以 2 倍速快速探测带宽；Drain 排空队列；ProbeBW 以 1.25 倍速周期性探测带宽增长，以 0.75 倍速排空；ProbeRTT 定期降低发送速率以测量最小 RTT。BBR 的发送速率设为 BtlBw × pacing_gain，在途数据量（BDP）设为 BtlBw × RTprop，使数据恰好在瓶颈链路上以满速率传输而不积压队列。BBR 在高带宽延迟积网络和浅缓冲路由器环境下表现优于 CUBIC，但可能与其他流公平性不足。",
       [
           ("BBR", ["bottleneck bandwidth and RTT", "BBR"]),
           ("拥塞控制", ["congestion control"]),
           ("瓶颈带宽", ["bottleneck bandwidth", "BtlBw"]),
           ("RTT", ["round trip time", "往返时延"]),
           ("CUBIC", ["CUBIC"]),
           ("带宽延迟积", ["bandwidth delay product", "BDP"]),
           ("在途数据量", ["in-flight data", "bytes in flight"]),
           ("Pacing", ["pacing", " pacing rate"]),
       ],
       chain=["计算机网络", "传输层协议", "BBR"],
       tags=["cross_lang", "depth_sensitive"]),

    # ── depth 4 ──
    qa("net_017", "计算机网络", 4,
       "HTTP/2 多路复用的实现细节",
       "HTTP/2 的多路复用允许在单个 TCP 连接上同时发送多个请求和响应，解决了 HTTP/1.1 的队头阻塞问题。HTTP/2 将每个请求/响应分解为一个或多个 Stream，每个 Stream 进一步分解为 Frame（帧）。帧是 HTTP/2 通信的最小单位，带有 Stream ID 标识所属流。发送端将不同 Stream 的帧交错发送，接收端按 Stream ID 重组。帧类型包括 DATA（数据）、HEADERS（头部）、SETTINGS（设置）、WINDOW_UPDATE（流量控制）等。HTTP/2 的头部压缩使用 HPACK 算法，通过静态表、动态表和哈夫曼编码大幅减少头部开销。服务端推送允许服务器在客户端请求前主动推送相关资源。HTTP/2 仍保留了 TCP 层的队头阻塞——单个 TCP 包丢失会阻塞所有 Stream，HTTP/3 通过 QUIC 协议在 UDP 上解决了此问题。",
       [
           ("HTTP/2", ["HTTP 2.0", "H2"]),
           ("多路复用", ["multiplexing", "多路复用"]),
           ("Stream", ["stream", "流"]),
           ("Frame", ["frame", "帧"]),
           ("HPACK", ["HPACK", "HTTP/2头部压缩"]),
           ("服务端推送", ["server push", "HTTP/2推送"]),
           ("队头阻塞", ["head of line blocking", "HOL阻塞"]),
           ("HTTP/3", ["HTTP 3.0", "H3"]),
           ("QUIC", ["QUIC", "quick UDP internet connections"]),
       ],
       chain=["计算机网络", "应用层协议", "HTTP", "HTTP/2"],
       tags=["cross_lang", "depth_sensitive"]),

    qa("net_018", "计算机网络", 4,
       "BGP 路由协议的选路过程",
       "BGP（边界网关协议）是互联网的核心路由协议，负责自治系统（AS）之间的路由交换。BGP 是路径矢量协议，路由决策通过 13 步属性比较完成。主要选路属性依次为：权重（Weight，Cisco 私有，本地优先）、本地优先级（Local Preference，AS 内）、本地起源（Network/Aggregate 声明优于 BGP 学习）、AS 路径长度（AS_PATH，越短越优）、起源代码（IGP > EGP > Incomplete）、MED（多出口鉴别器，影响入站流量）、eBGP 优于 iBGP、IGP 度量到下一跳的距离。BGP 的路由策略通过 Route Map 和 Community 属性实现精细控制。BGP 收敛较慢，默认不传播路由，需显式配置。BGP 的稳定性依赖路由抖动抑制和 Graceful Restart 机制。BGP 安全性是互联网的薄弱环节，RPKI 可验证路由起源。",
       [
           ("BGP", ["border gateway protocol", "边界网关协议"]),
           ("自治系统", ["autonomous system", "AS"]),
           ("AS路径", ["AS_PATH", "AS_PATH属性"]),
           ("本地优先级", ["local preference", "LOCAL_PREF"]),
           ("MED", ["multi-exit discriminator", "多出口鉴别器"]),
           ("路由策略", ["routing policy", "Route Map"]),
           ("路由抖动抑制", ["route flap damping", "路由抖动抑制"]),
           ("RPKI", ["resource public key infrastructure", "RPKI"]),
       ],
       chain=["计算机网络", "路由协议", "外部网关协议", "BGP"],
       tags=["cross_lang", "depth_sensitive"]),

    # ── depth 5 ──
    qa("net_019", "计算机网络", 5,
       "QUIC 协议如何解决 TCP 队头阻塞？",
       "QUIC（Quick UDP Internet Connections）是 Google 设计、IETF 标准化的传输层协议，运行在 UDP 之上。QUIC 解决 TCP 队头阻塞的核心机制是 Stream 级别的独立重传。在 HTTP/2 over TCP 中，一个 TCP 包丢失会阻塞该连接上所有 Stream 的数据交付，因为 TCP 保证有序交付。QUIC 将每个 Stream 独立编号，Stream 内部保证有序，但 Stream 之间无序——一个 Stream 的包丢失只影响该 Stream，其他 Stream 的数据可正常交付给应用层。QUIC 的其他关键特性：连接迁移通过 Connection ID 实现，IP 变化（如 WiFi 切 4G）时连接不断；0-RTT 握手复用 TLS 1.3 会话恢复；1-RTT 初始握手集成 TLS 加密，减少握手往返；用户态实现便于快速迭代，无需内核更新。",
       [
           ("QUIC", ["QUIC", "quick UDP internet connections"]),
           ("队头阻塞", ["head of line blocking", "HOL阻塞"]),
           ("Stream", ["stream", "QUIC流"]),
           ("连接迁移", ["connection migration", "连接迁移"]),
           ("Connection ID", ["connection id", "CID"]),
           ("0-RTT", ["zero round trip time"]),
           ("TLS 1.3", ["TLS 1.3"]),
           ("HTTP/3", ["HTTP 3.0", "H3"]),
       ],
       chain=["计算机网络", "传输层协议", "HTTP/2优化", "队头阻塞问题", "QUIC"],
       tags=["cross_lang", "depth_sensitive"]),

    qa("net_020", "计算机网络", 5,
       "eBPF 在网络数据包处理中的应用",
       "eBPF（extended Berkeley Packet Filter）是 Linux 内核中的可编程虚拟机，允许在内核态安全地运行沙箱程序而不需修改内核源码或加载内核模块。在网络数据包处理中，eBPF 程序挂载到网络钩子（XDP、TC、socket）上对数据包进行高效处理。XDP（eXpress Data Path）在网卡驱动层提供最早的数据包处理点，适用于高性能防火墙、DDoS 防护和负载均衡，延迟低至纳秒级。TC（Traffic Control）在内核网络栈的入口/出口提供更丰富的数据包操作。eBPF 的优势包括：内核态执行避免用户态切换开销；JIT 编译为原生指令；验证器保证程序安全终止且不破坏内核。Cilium 利用 eBPF 实现容器网络的网络策略和服务负载均衡，替代传统 iptables。eBPF 正在重塑内核网络数据面的处理方式。",
       [
           ("eBPF", ["extended berkeley packet filter", "eBPF"]),
           ("XDP", ["express data path", "XDP"]),
           ("TC", ["traffic control", "流量控制"]),
           ("Cilium", ["Cilium"]),
           ("iptables", ["iptables"]),
           ("JIT编译", ["just in time compilation", "JIT"]),
           ("DDoS防护", ["DDoS protection"]),
           ("内核态", ["kernel space", "内核空间"]),
       ],
       chain=["计算机网络", "数据包处理", "内核网络", "高性能数据包处理", "eBPF"],
       tags=["cross_lang", "depth_sensitive"]),
]


# ══════════════════════════════════════════════════════
# 领域 3: 数据库系统
# ══════════════════════════════════════════════════════

DB_QA = [
    qa("db_001", "数据库系统", 1,
       "事务的 ACID 特性是什么？",
       "ACID 是数据库事务正确可靠的四个保证。原子性（Atomicity）指事务中的操作要么全部执行成功，要么全部不执行，通过 undo log 回滚未完成的事务。一致性（Consistency）指事务执行前后数据库从一个合法状态转换到另一个合法状态，由应用层约束和数据库完整性约束共同保证。隔离性（Isolation）指并发事务互不干扰，通过锁机制和 MVCC 实现，不同隔离级别提供不同程度的隔离。持久性（Durability）指事务提交后对数据的修改永久保存，通过 redo log 和 WAL（预写日志）保证即使系统崩溃也能恢复。ACID 中原子性和持久性由日志机制保证，隔离性由并发控制保证，一致性是前三者的结果。",
       [
           ("ACID", ["ACID", "ACID特性"]),
           ("原子性", ["atomicity"]),
           ("一致性", ["consistency"]),
           ("隔离性", ["isolation"]),
           ("持久性", ["durability"]),
           ("undo log", ["undo log", "回滚日志"]),
           ("redo log", ["redo log", "重做日志"]),
           ("WAL", ["write ahead logging", "预写日志"]),
           ("MVCC", ["multi-version concurrency control", "多版本并发控制"]),
       ],
       tags=["cross_lang"]),

    qa("db_002", "数据库系统", 1,
       "数据库索引的原理和类型",
       "数据库索引是加速查询的数据结构，以空间换时间。最常见的索引类型是 B+ 树索引，其所有数据存储在叶子节点，叶子节点通过链表连接，支持范围查询和排序。B+ 树的非叶子节点只存储索引键，一个节点可容纳更多键，降低树的高度，减少磁盘 I/O。聚簇索引将数据和主键索引存储在一起，一张表只能有一个聚簇索引；非聚簇索引（二级索引）存储主键值，查询非索引列需回表。哈希索引基于哈希表，只支持等值查询，不支持范围查询。联合索引遵循最左前缀原则，查询条件需从索引最左列开始使用。覆盖索引指查询的列全部包含在索引中，无需回表。索引虽加速查询但会降低写入速度并占用存储空间。",
       [
           ("B+树", ["B+ tree", "B+树索引"]),
           ("聚簇索引", ["clustered index", "聚集索引"]),
           ("非聚簇索引", ["non-clustered index", "二级索引", "辅助索引"]),
           ("哈希索引", ["hash index"]),
           ("联合索引", ["composite index", "复合索引", "联合索引"]),
           ("最左前缀", ["leftmost prefix rule", "最左前缀原则"]),
           ("覆盖索引", ["covering index"]),
           ("回表", ["table lookup", "回表查询"]),
       ],
       tags=["cross_lang"]),

    qa("db_003", "数据库系统", 1,
       "数据库锁机制有哪些？",
       "数据库锁机制保证并发事务的隔离性。按粒度分：表锁锁定整张表，开销小但并发度低；行锁锁定单行数据，并发度高但开销大；间隙锁锁定索引区间，防止幻读。按类型分：共享锁（S 锁）允许并发读但阻止写；排他锁（X 锁）阻止其他读写；意向锁（IS/IX）是表级锁，表示事务打算在行级加 S/X 锁，提高锁冲突检测效率。两阶段锁协议要求事务在增长阶段获取锁、在收缩阶段释放锁，遵循此协议可保证可串行化。死锁是两个或多个事务互相等待对方释放锁，数据库通过等待图检测或超时机制处理。MySQL InnoDB 的 Next-Key Lock 结合了行锁和间隙锁，在可重复读隔离级别下防止幻读。乐观锁通过版本号实现，适合读多写少场景。",
       [
           ("表锁", ["table lock", "表级锁"]),
           ("行锁", ["row lock", "行级锁"]),
           ("间隙锁", ["gap lock"]),
           ("共享锁", ["shared lock", "S锁", "读锁"]),
           ("排他锁", ["exclusive lock", "X锁", "写锁"]),
           ("意向锁", ["intention lock", "IS锁", "IX锁"]),
           ("两阶段锁", ["two-phase locking", "2PL"]),
           ("死锁", ["deadlock"]),
           ("Next-Key Lock", ["next-key lock", "临键锁"]),
           ("乐观锁", ["optimistic lock"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("db_004", "数据库系统", 1,
       "MVCC 的原理是什么？",
       "MVCC（多版本并发控制）通过为每行数据维护多个版本，使读操作不阻塞写操作、写操作不阻塞读操作，大幅提升并发性能。以 MySQL InnoDB 为例，每行数据包含两个隐藏列：创建版本号（trx_id）和删除版本号（roll_pointer）。事务修改数据时创建新版本而非覆盖旧版本。读操作根据事务的 Read View 判断哪个版本对当前事务可见。Read View 记录当前活跃事务列表，版本号小于最小活跃事务号的版本可见，大于最大事务号的不可见，介于之间的需要比较具体事务。MVCC 在 RC（读已提交）级别下每次 SELECT 生成新 Read View，在 RR（可重复读）级别下只在第一次 SELECT 时生成。MVCC 解决了读写冲突，但长事务会累积大量旧版本导致空间膨胀，需定期 VACUUM 或 purge 清理。",
       [
           ("MVCC", ["multi-version concurrency control", "多版本并发控制"]),
           ("Read View", ["read view", "读视图"]),
           ("事务ID", ["transaction id", "trx_id"]),
           ("版本链", ["version chain", "undo log版本链"]),
           ("隔离级别", ["isolation level", "事务隔离级别"]),
           ("读已提交", ["read committed", "RC"]),
           ("可重复读", ["repeatable read", "RR"]),
           ("VACUUM", ["vacuum", "空间回收"]),
           ("undo log", ["undo log", "回滚日志"]),
       ],
       tags=["alias_heavy", "cross_lang"]),

    qa("db_005", "数据库系统", 1,
       "分库分表的策略和注意事项",
       "分库分表是将数据分散到多个数据库或表中，解决单库单表性能瓶颈。分库分表策略包括：水平分表按行拆分，常用分片键为用户 ID 或时间，分片算法有哈希分片（均匀分布）、范围分片（便于范围查询）和一致性哈希（减少扩容数据迁移）；垂直分表按列拆分，将大字段或冷数据分离。分库分表带来的挑战：跨库 JOIN 难以执行，需通过应用层组装或数据冗余解决；分布式事务复杂，可用两阶段提交、TCC 或 Saga 模式；全局唯一 ID 需用雪花算法或号段模式；跨库分页和聚合查询性能差。中间件如 ShardingSphere 和 MyCat 提供透明化分库分表。建议在单表数据超过千万行且性能优化无效时才考虑分库分表，优先利用索引优化和读写分离。",
       [
           ("分库分表", ["sharding", "数据库分片"]),
           ("水平分表", ["horizontal sharding", "水平分片"]),
           ("垂直分表", ["vertical sharding", "垂直分片"]),
           ("分片键", ["sharding key", "分片字段"]),
           ("一致性哈希", ["consistent hashing"]),
           ("雪花算法", ["snowflake", "雪花ID"]),
           ("分布式事务", ["distributed transaction"]),
           ("ShardingSphere", ["ShardingSphere"]),
           ("TCC", ["try confirm cancel", "TCC"]),
       ],
       tags=["cross_lang"]),

    qa("db_006", "数据库系统", 1,
       "主从复制的原理和常见拓扑",
       "主从复制将主库的数据变更同步到从库，实现读写分离、数据备份和高可用。MySQL 主从复制基于 binlog（二进制日志）：主库执行写操作后将变更记录到 binlog，从库的 IO 线程拉取 binlog 并写入 relay log（中继日志），从库的 SQL 线程重放 relay log 中的操作。复制方式有三种：异步复制（默认，主库不等待从库确认，可能丢数据）、半同步复制（至少一个从库确认后才提交，平衡性能与安全）、全同步复制（所有从库确认，性能最差）。复制格式包括基于语句（STATEMENT）、基于行（ROW）和混合模式（MIXED）。常见拓扑：一主多从（读写分离）、主主复制（双写需解决冲突）、级联复制（减少主库压力）。主从延迟是常见问题，通过并行复制（基于组提交或 WriteSet）缓解。",
       [
           ("主从复制", ["master-slave replication", "主从同步"]),
           ("binlog", ["binary log", "二进制日志", "binlog"]),
           ("relay log", ["relay log", "中继日志"]),
           ("异步复制", ["asynchronous replication"]),
           ("半同步复制", ["semi-synchronous replication"]),
           ("读写分离", ["read-write splitting", "读写分离"]),
           ("主从延迟", ["replication lag", "复制延迟"]),
           ("并行复制", ["parallel replication", "多线程复制"]),
       ],
       tags=["cross_lang"]),

    qa("db_007", "数据库系统", 1,
       "Redis 缓存的常见问题和解决方案",
       "Redis 缓存在高并发场景下面临三个经典问题。缓存穿透指查询不存在的数据，请求穿透到数据库，解决方案包括缓存空值（设置短 TTL）和布隆过滤器拦截。缓存击穿指热点 key 过期瞬间大量请求打到数据库，解决方案包括互斥锁（只允许一个线程重建缓存）和热点数据永不过期（异步更新）。缓存雪崩指大量 key 同时过期或 Redis 宕机，解决方案包括 TTL 加随机抖动避免集中过期、多级缓存（本地缓存 + Redis）和熔断降级。缓存一致性问题：更新数据库后需同步更新或删除缓存，常用策略是 Cache Aside（先更新数据库再删除缓存）、延迟双删和基于 binlog 的异步同步。Redis 持久化通过 RDB 快照和 AOF 日志实现，各有优劣。",
       [
           ("缓存穿透", ["cache penetration", "缓存穿透"]),
           ("缓存击穿", ["cache breakdown", "缓存击穿", "热点key"]),
           ("缓存雪崩", ["cache avalanche", "缓存雪崩"]),
           ("布隆过滤器", ["bloom filter"]),
           ("互斥锁", ["mutex lock", "互斥锁"]),
           ("Cache Aside", ["cache aside", "旁路缓存"]),
           ("延迟双删", ["delayed double deletion"]),
           ("RDB", ["redis database", "RDB快照"]),
           ("AOF", ["append only file", "AOF日志"]),
       ],
       tags=["cross_lang"]),

    qa("db_008", "数据库系统", 1,
       "SQL 查询优化的关键技巧",
       "SQL 查询优化是提升数据库性能的核心手段。首先应分析执行计划（EXPLAIN），关注是否使用索引、扫描行数、连接类型和临时表使用。索引优化：确保 WHERE、JOIN、ORDER BY 条件使用索引，避免索引失效场景如函数操作、类型隐式转换、LIKE 前缀通配符、OR 条件中有非索引列。查询优化：只查需要的列避免 SELECT *，大分页用游标或延迟关联替代 LIMIT OFFSET，避免子查询用 JOIN 替代。JOIN 优化：小表驱动大表，被驱动表的连接列有索引，减少嵌套循环次数。聚合优化：GROUP BY 列尽量有索引，避免临时表排序。写入优化：批量插入替代单条插入，合理设置事务大小避免长事务。数据库配置优化：调整缓冲池大小、连接池、排序缓冲区等参数。",
       [
           ("执行计划", ["execution plan", "EXPLAIN", "查询计划"]),
           ("索引失效", ["index failure", "索引失效"]),
           ("大分页优化", ["pagination optimization", "深度分页"]),
           ("延迟关联", ["deferred join", "延迟关联"]),
           ("小表驱动大表", ["small table drives big table", "小表驱动"]),
           ("SELECT *", ["select star", "全表查询"]),
           ("缓冲池", ["buffer pool", "InnoDB缓冲池"]),
           ("连接池", ["connection pool"]),
       ],
       tags=["cross_lang"]),

    qa("db_009", "数据库系统", 1,
       "数据库范式的作用和局限",
       "数据库范式是关系数据库设计的规范化规则，目的是减少数据冗余和更新异常。第一范式（1NF）要求每列不可再分（原子性）。第二范式（2NF）在 1NF 基础上要求非主属性完全依赖主键，消除部分依赖。第三范式（3NF）在 2NF 基础上消除非主属性对主键的传递依赖。BCNF 是 3NF 的加强版，要求每个决定因素都是候选键。范式的优势是数据冗余低、一致性好，但高度规范化会导致表数量增多、JOIN 操作频繁、查询性能下降。实际应用中常适度反范式化，通过冗余字段减少 JOIN，在冗余和性能间权衡。OLTP 系统通常遵循 3NF，OLAP/数据仓库常采用星型或雪花模型做大量反范式化。反范式化需要应用层维护数据一致性。",
       [
           ("数据库范式", ["database normalization", "范式"]),
           ("第一范式", ["first normal form", "1NF"]),
           ("第二范式", ["second normal form", "2NF"]),
           ("第三范式", ["third normal form", "3NF"]),
           ("BCNF", ["boyce codd normal form", "BCNF"]),
           ("反范式化", ["denormalization", "反范式"]),
           ("星型模型", ["star schema", "星型模型"]),
           ("雪花模型", ["snowflake schema", "雪花模型"]),
           ("OLAP", ["online analytical processing", "联机分析处理"]),
       ],
       tags=["cross_lang"]),

    qa("db_010", "数据库系统", 1,
       "JOIN 的类型和执行机制",
       "JOIN 是关系数据库中连接多张表的操作。JOIN 类型包括：内连接（INNER JOIN）返回两表匹配的行；左外连接（LEFT JOIN）返回左表所有行及右表匹配行，右表无匹配则为 NULL；右外连接（RIGHT JOIN）类似但方向相反；全外连接（FULL JOIN）返回两表所有行；交叉连接（CROSS JOIN）返回笛卡尔积。JOIN 的执行算法有三种：嵌套循环连接（Nested Loop）对驱动表每行扫描被驱动表，适合小表驱动大表且被驱动表有索引；块嵌套循环连接（BNL）将驱动表数据分块缓存，减少被驱动表扫描次数；哈希连接（Hash Join）对被驱动表构建哈希表后探测，适合等值连接且无索引的大表。MySQL 8.0 引入 Hash Join 替代 BNL。优化器基于成本选择 JOIN 顺序和算法。",
       [
           ("内连接", ["inner join", "INNER JOIN"]),
           ("左外连接", ["left join", "LEFT JOIN", "左连接"]),
           ("全外连接", ["full join", "FULL JOIN"]),
           ("嵌套循环连接", ["nested loop join", "NLJ"]),
           ("块嵌套循环", ["block nested loop", "BNL"]),
           ("哈希连接", ["hash join", "Hash Join"]),
           ("笛卡尔积", ["cartesian product", "交叉连接"]),
           ("驱动表", ["driving table", "驱动表"]),
       ],
       tags=["cross_lang"]),

    qa("db_011", "数据库系统", 1,
       "数据库连接池的作用和配置",
       "数据库连接池预先创建并维护一组数据库连接，避免频繁创建和销毁连接的开销。TCP 连接建立需要三次握手加认证，频繁建连在高并发下成为瓶颈。连接池的核心参数包括：最小空闲连接数保持基础连接就绪；最大连接数限制并发上限；连接超时控制获取连接的等待时间；空闲连接超时回收长时间不用的连接；最大生命周期定期重建连接防止连接老化。连接池还需处理连接有效性检测（通过心跳查询验证连接可用性）和连接泄漏检测（连接借出后长时间未归还）。常见连接池实现有 HikariCP（性能最优）、Druid（阿里开源，监控功能强）和 DBCP。HikariCP 的性能优势来源于无锁设计、FastList 和 ConcurrentBag 等优化。连接池配置需根据数据库最大连接数、应用并发量和事务时长综合考虑。",
       [
           ("连接池", ["connection pool", "数据库连接池"]),
           ("HikariCP", ["HikariCP", "Hikari"]),
           ("Druid", ["Druid", "Druid连接池"]),
           ("最大连接数", ["max pool size", "最大连接数"]),
           ("空闲连接", ["idle connection", "空闲连接"]),
           ("连接泄漏", ["connection leak", "连接泄漏检测"]),
           ("连接超时", ["connection timeout"]),
           ("心跳检测", ["heartbeat", "连接健康检查"]),
       ],
       tags=["cross_lang"]),

    qa("db_012", "数据库系统", 1,
       "慢查询分析和优化流程",
       "慢查询分析是数据库性能调优的核心环节。首先开启慢查询日志，设置 long_query_time 阈值（如 1s）记录执行时间超过阈值的 SQL。使用 pt-query-digest 或 mysqldumpslow 工具聚合分析慢查询日志，按总执行时间、调用次数、平均时间排序，优先优化影响最大的查询。对目标 SQL 执行 EXPLAIN 分析执行计划：关注 type 列（ALL 全表扫描最差，const/eq_ref 最优）、key 列（实际使用的索引）、rows 列（预估扫描行数）、Extra 列（Using temporary/filesort 需优化）。常见优化方向：添加合适索引消除全表扫描，重构 SQL 避免索引失效，拆分复杂查询，使用覆盖索引避免回表，优化 JOIN 顺序。持续监控慢查询数量和最大执行时间，建立性能基线和告警机制。",
       [
           ("慢查询", ["slow query", "慢查询日志"]),
           ("EXPLAIN", ["EXPLAIN", "执行计划"]),
           ("全表扫描", ["full table scan", "ALL"]),
           ("pt-query-digest", ["pt-query-digest", "Percona工具"]),
           ("Using temporary", ["using temporary", "临时表"]),
           ("Using filesort", ["using filesort", "文件排序"]),
           ("覆盖索引", ["covering index"]),
           ("性能基线", ["performance baseline", "性能基线"]),
       ],
       tags=["cross_lang"]),

    qa("db_013", "数据库系统", 1,
       "分区表的作用和类型",
       "分区表将一张大表在物理存储上拆分为多个分区，对应用透明，SQL 无需修改。分区的主要目的是提升可管理性和查询性能：按分区裁剪只扫描相关分区，减少 I/O；分区独立维护，支持分区级归档、删除和索引重建。MySQL 支持四种分区类型：范围分区（RANGE）按列值范围划分，常按时间分区；列表分区（LIST）按离散值划分；哈希分区（HASH）按哈希值均匀分布；键分区（KEY）类似哈希但使用 MySQL 内部哈希函数。分区键必须是主键/唯一键的一部分。分区的局限：跨分区查询性能不一定提升，分区数过多增加管理开销，不支持外键。与分库分表不同，分区是单库内的物理拆分，不涉及分布式事务问题。分区适合按时间维度查询和归档的场景如日志表、订单表。",
       [
           ("分区表", ["partition table", "表分区"]),
           ("范围分区", ["range partition", "RANGE分区"]),
           ("列表分区", ["list partition", "LIST分区"]),
           ("哈希分区", ["hash partition", "HASH分区"]),
           ("分区裁剪", ["partition pruning", "分区裁剪"]),
           ("分库分表", ["sharding"]),
           ("归档", ["archiving", "数据归档"]),
       ],
       tags=["cross_lang"]),

    qa("db_014", "数据库系统", 1,
       "CAP 理论和 BASE 理论",
       "CAP 理论指出分布式系统在一致性（Consistency）、可用性（Availability）和分区容错性（Partition tolerance）三者中最多同时满足两个。在网络分区不可避免的前提下，实际选择是在 CP 和 AP 之间。CP 系统如 ZooKeeper、etcd 在分区时拒绝写入保证一致性；AP 系统如 Cassandra、Eureka 在分区时继续服务牺牲强一致性。BASE 理论是 CAP 在 AP 方向的实践：基本可用（Basically Available）允许部分功能降级；软状态（Soft State）允许数据存在中间状态；最终一致性（Eventually Consistent）保证数据最终收敛一致。分布式系统的一致性模型从强到弱包括：线性一致性、顺序一致性、因果一致性和最终一致性。现代分布式数据库如 Spanner 通过 TrueTime 实现外部一致性，TiDB 通过 Raft + MVCC 提供强一致性。",
       [
           ("CAP", ["CAP theorem", "CAP理论"]),
           ("一致性", ["consistency", "C"]),
           ("可用性", ["availability", "A"]),
           ("分区容错", ["partition tolerance", "P"]),
           ("BASE", ["BASE理论", "BASE"]),
           ("最终一致性", ["eventual consistency"]),
           ("线性一致性", ["linearizability", "线性化"]),
           ("Raft", ["Raft共识算法", "Raft"]),
       ],
       tags=["cross_lang"]),

    # ── depth 3 ──
    qa("db_015", "数据库系统", 3,
       "InnoDB 的 Change Buffer 机制",
       "Change Buffer 是 InnoDB 对非唯一二级索引的写入优化机制。当修改非唯一二级索引页时，如果目标页不在缓冲池中，InnoDB 不立即从磁盘读取该页，而是将修改记录在 Change Buffer 中，后续读取该页时再合并（merge）这些修改。这大幅减少了随机 I/O，因为二级索引的修改通常是随机的。Change Buffer 只对非唯一索引有效，因为唯一索引必须读取页来检查唯一性约束。Change Buffer 的大小由 innodb_change_buffer_max_size 参数控制（默认占缓冲池 25%）。Change Buffer 的数据也会写入 ibdata 系统表空间，保证崩溃恢复。在写多读少的场景下 Change Buffer 收益显著，但在写后立即读的场景下反而增加开销（merge 操作）。Change Buffer 在 MySQL 5.6 后支持全部 DML 操作（INSERT/DELETE/UPDATE）。",
       [
           ("Change Buffer", ["change buffer", "插入缓冲"]),
           ("二级索引", ["secondary index", "非聚簇索引", "辅助索引"]),
           ("缓冲池", ["buffer pool", "InnoDB缓冲池"]),
           ("随机I/O", ["random IO", "随机读写"]),
           ("合并", ["merge", "Change Buffer合并"]),
           ("唯一索引", ["unique index"]),
           ("崩溃恢复", ["crash recovery"]),
       ],
       chain=["数据库系统", "存储引擎", "InnoDB"],
       tags=["cross_lang", "depth_sensitive"]),

    qa("db_016", "数据库系统", 3,
       "MySQL 的两阶段提交机制",
       "MySQL InnoDB 的两阶段提交（2PC）保证 redo log 和 binlog 的数据一致性。第一阶段（Prepare 阶段）：InnoDB 将事务状态设为 PREPARE，写入 redo log 并刷盘，记录 XID（事务 ID）。第二阶段（Commit 阶段）：先写入 binlog 并刷盘，然后将 redo log 的事务状态改为 COMMIT。崩溃恢复时根据 redo log 中的事务状态和 binlog 是否完整决定提交或回滚：如果 redo log 是 PREPARE 状态且 binlog 有完整 XID，则提交；如果 redo log 是 PREPARE 状态但 binlog 无对应 XID，则回滚；如果 redo log 是 COMMIT 状态，则已提交。两阶段提交保证了即使崩溃也不会出现 redo log 和 binlog 不一致的情况，这对主从复制的正确性至关重要。组提交（Group Commit）优化将多个事务的 binlog 刷盘合并，减少 fsync 次数。",
       [
           ("两阶段提交", ["two phase commit", "2PC", "两阶段提交"]),
           ("redo log", ["redo log", "重做日志"]),
           ("binlog", ["binary log", "二进制日志"]),
           ("XID", ["transaction id", "XID", "事务标识符"]),
           ("崩溃恢复", ["crash recovery", "崩溃恢复"]),
           ("Prepare", ["prepare", "预提交"]),
           ("组提交", ["group commit", "组提交"]),
           ("fsync", ["fsync", "刷盘"]),
       ],
       chain=["数据库系统", "事务机制", "两阶段提交"],
       tags=["cross_lang", "depth_sensitive"]),

    # ── depth 4 ──
    qa("db_017", "数据库系统", 4,
       "LSM-Tree 的写入和合并机制",
       "LSM-Tree（Log-Structured Merge-Tree）是 LevelDB、RocksDB、Cassandra 等系统使用的存储引擎架构，通过将随机写转换为顺序写来优化写入性能。写入流程：数据先写入内存中的 MemTable（跳表结构），同时追加到 WAL（预写日志）保证持久性。当 MemTable 达到阈值后变为不可变的 Immutable MemTable，后台线程将其刷盘为 SSTable（有序字符串表）文件，存入 Level 0。读取时按 MemTable → Level 0 → Level 1 → ... 逐级查找。Compaction（合并）是 LSM-Tree 的核心机制：Level 0 的 SSTable 可能重叠，需合并到 Level 1 消除重叠；Level 1 及以上每层有序且不重叠。Compaction 分为 Size-Tiered（同层合并，写放大小但读放大大）和 Leveled（跨层合并，读放大小但写放大大）。RocksDB 支持混合策略。LSM-Tree 的代价是读放大（多级查找）和空间放大（多版本数据），通过 Bloom Filter 和 Compaction 缓解。",
       [
           ("LSM-Tree", ["log structured merge tree", "LSM树"]),
           ("MemTable", ["memtable", "内存表"]),
           ("SSTable", ["sorted string table", "SSTable", "有序字符串表"]),
           ("Compaction", ["compaction", "合并压缩"]),
           ("WAL", ["write ahead log", "预写日志"]),
           ("Bloom Filter", ["bloom filter", "布隆过滤器"]),
           ("写放大", ["write amplification"]),
           ("读放大", ["read amplification"]),
           ("RocksDB", ["RocksDB"]),
       ],
       chain=["数据库系统", "存储引擎", "LSM-Tree架构", "Compaction"],
       tags=["cross_lang", "depth_sensitive"]),

    qa("db_018", "数据库系统", 4,
       "Raft 共识算法的领导者选举",
       "Raft 是易于理解的分布式共识算法，通过领导者（Leader）模式简化分布式协调。节点有三种角色：跟随者（Follower）、候选人（Candidate）和领导者（Leader）。领导者选举过程：初始所有节点为 Follower，若在选举超时（election timeout）内未收到领导者的心跳，Follower 转为 Candidate 并发起选举。Candidate 先给自己投票，然后递增任期号（term）并向其他节点发送 RequestVote RPC。如果 Candidate 获得多数节点投票则成为 Leader。为确保安全，每个节点在一个任期内只能投一票，且候选人的日志必须至少与自己一样新（up-to-date）。选举超时随机化（如 150-300ms）避免多个 Candidate 同时竞选导致活锁。Leader 通过周期性心跳维持权威。脑裂情况下，少数派分区无法获得多数投票，不会产生新 Leader，保证同一任期最多一个 Leader。Raft 的日志复制要求 Leader 将日志条目复制到多数节点后才提交。",
       [
           ("Raft", ["Raft共识算法", "Raft"]),
           ("领导者选举", ["leader election", "Leader选举"]),
           ("任期", ["term", "任期号"]),
           ("RequestVote", ["request vote RPC", "投票请求"]),
           ("选举超时", ["election timeout"]),
           ("心跳", ["heartbeat", "心跳机制"]),
           ("脑裂", ["split brain", "脑裂"]),
           ("日志复制", ["log replication", "日志复制"]),
       ],
       chain=["数据库系统", "分布式共识", "Raft算法", "领导者选举"],
       tags=["cross_lang", "depth_sensitive"]),

    # ── depth 5 ──
    qa("db_019", "数据库系统", 5,
       "Spanner 的 TrueTime 如何实现外部一致性？",
       "Spanner 是 Google 的全球分布式数据库，通过 TrueTime API 实现外部一致性（线性一致性）。TrueTime 将时间表示为区间 [TT.after, TT.before] 而非点值，承认时钟不确定性。每个数据中心部署 GPS 接收器和原子钟作为时间主服务器，将误差控制在 7ms 以内（平均 4ms）。Spanner 的 Commit Wait 机制保证外部一致性：事务提交时获取时间戳 s = TT.after()，然后等待直到 TT.before() < s（即等待 TrueTime 不确定性消除）才返回提交成功。这确保了提交时间戳 s 一定大于所有先前提交事务的时间戳。对于只读事务，Spanner 选择 Leader 副本的最后分配时间戳作为 safe timestamp，保证读取到所有已提交数据。2PC 协调者选择 prepare 时间戳，Leader 选择 commit 时间戳并执行 Commit Wait。Paxos 组管理每个分片的副本一致性。TrueTime 的 Commit Wait 增加了提交延迟，但保证了跨数据中心的全局一致性。",
       [
           ("Spanner", ["Google Spanner", "Spanner"]),
           ("TrueTime", ["TrueTime API", "TrueTime"]),
           ("外部一致性", ["external consistency", "线性一致性"]),
           ("Commit Wait", ["commit wait", "提交等待"]),
           ("Paxos", ["Paxos共识算法", "Paxos"]),
           ("时钟不确定性", ["clock uncertainty", "时钟误差"]),
           ("GPS授时", ["GPS time", "GPS时钟"]),
           ("原子钟", ["atomic clock"]),
       ],
       chain=["数据库系统", "分布式共识", "全局时钟", "TrueTime", "外部一致性"],
       tags=["cross_lang", "depth_sensitive"]),

    qa("db_020", "数据库系统", 5,
       "TiDB 的分布式执行引擎如何处理 JOIN",
       "TiDB 是兼容 MySQL 协议的分布式 HTAP 数据库，采用存算分离架构：TiDB 节点负责 SQL 解析和执行，TiKV 节点负责分布式 KV 存储。分布式 JOIN 的执行策略由优化器基于成本选择。Hash Join：将小表（构建端）按 JOIN KEY 哈希到内存哈希表，大表（探测端）流式扫描匹配。当小表数据量超过内存阈值时，TiDB 将其分区落盘（grace hash join）。Broadcast Join：将小表广播到所有 TiKV 节点，与本地大表分区做本地 JOIN，避免网络 Shuffle。Index Join：利用外表的索引加速内表查找，适合外表小且内表 JOIN 列有索引的场景。TiDB 的执行器采用 Volcano 模型，算子间流水线传递数据。Coprocessor 下推将过滤、聚合等算子下推到 TiKV 执行，减少网络传输。统计信息（直方图、CMSketch）帮助优化器选择最优 JOIN 策略。MPP 模式（TiFlash）将大规模 JOIN 并行化到列存节点。",
       [
           ("TiDB", ["TiDB"]),
           ("TiKV", ["TiKV", "分布式KV存储"]),
           ("Hash Join", ["hash join", "哈希连接"]),
           ("Broadcast Join", ["broadcast join", "广播连接"]),
           ("Index Join", ["index join", "索引连接"]),
           ("Coprocessor", ["coprocessor", "协处理器下推"]),
           ("Volcano模型", ["volcano model", "火山模型"]),
           ("TiFlash", ["TiFlash", "列存引擎"]),
           ("MPP", ["massively parallel processing", "大规模并行处理"]),
       ],
       chain=["数据库系统", "分布式数据库", "查询执行", "分布式JOIN", "TiDB执行引擎"],
       tags=["cross_lang", "depth_sensitive"]),
]


# ══════════════════════════════════════════════════════
# 输出
# ══════════════════════════════════════════════════════

SCHEMA = {
    "$schema": "golden_set_v1.0",
    "description": "「伴你成长」学习 Agent 概念抽取评测 Golden Set",
    "qa_pair_format": {
        "qa_id": "str - 唯一标识 (domain_prefix_NNN)",
        "domain": "str - 领域名",
        "depth": "int - 探索树深度 (1=根问题, 3-5=层级累积评测)",
        "parent_concept_chain": "list[str] - 父概念链 (depth>1时必填)",
        "question": "str - 用户问题",
        "reference_answer": "str - 参考答案",
        "golden_concepts": [
            {
                "canonical_name": "str - 规范概念名",
                "aliases": "list[str] - 别名列表 (中英文/缩写)",
                "in_answer": "bool - 是否出现在参考答案中",
                "note": "str - 标注备注"
            }
        ],
        "tags": "list[str] - 标签 (alias_heavy/cross_lang/depth_sensitive)"
    },
    "matching_rule": "canonical_name + aliases 双轨匹配: 规范名精确匹配 / 预测规范名命中golden别名 / 预测别名命中golden规范名 / 别名交集",
    "domains": ["机器学习", "计算机网络", "数据库系统"],
    "target_metrics": {
        "extraction_f1_gate": 0.80,
        "false_merge_rate_gate": 0.05,
        "merge_recall_gate": 0.85
    }
}


def write_domain(filename, qa_list, domain_name):
    """写入单个领域的 golden set JSON"""
    output = {
        "domain": domain_name,
        "version": "v1.0",
        "qa_count": len(qa_list),
        "depth_distribution": {
            str(d): sum(1 for q in qa_list if q["depth"] == d)
            for d in sorted(set(q["depth"] for q in qa_list))
        },
        "qa_pairs": qa_list,
    }
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  {filename}: {len(qa_list)} QA pairs")
    return path


if __name__ == "__main__":
    print("Generating Golden Set...")

    # 写入 schema
    schema_path = os.path.join(OUTPUT_DIR, "schema.json")
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(SCHEMA, f, ensure_ascii=False, indent=2)
    print(f"  schema.json: schema definition")

    # 写入三个领域
    write_domain("machine_learning.json", ML_QA, "机器学习")
    write_domain("computer_networks.json", NET_QA, "计算机网络")
    write_domain("database_systems.json", DB_QA, "数据库系统")

    total = len(ML_QA) + len(NET_QA) + len(DB_QA)
    total_concepts = sum(len(q["golden_concepts"]) for q in ML_QA + NET_QA + DB_QA)
    print(f"\nTotal: {total} QA pairs, {total_concepts} golden concepts")
    print(f"Output directory: {OUTPUT_DIR}")
