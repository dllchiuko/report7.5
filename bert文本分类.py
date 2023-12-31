# 1 导入必要的库
import pandas as pd
import numpy as np
import json
import time
from tqdm import tqdm
from sklearn.metrics import accuracy_score, classification_report
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import TensorDataset, DataLoader, RandomSampler, SequentialSampler
from transformers import BertModel, BertConfig, BertTokenizer, AdamW, get_cosine_schedule_with_warmup
import warnings
warnings.filterwarnings('ignore')

# 初始化分词器
bert_path = 'bert_model/'
tokenizers = BertTokenizer.from_pretrained(bert_path)
max_len = 30

# 2 预处理数据集
input_ids = []
input_masks = []
input_types = []
labels = []
with open('dataset.csv', encoding='utf-8') as f:
    for i, line in tqdm(enumerate(f)):
        title, y = line.strip().split('\t')
        encode_dict = tokenizers.encode_plus(text=title, max_length=max_len, padding='max_length', truncation=True)

        input_ids.append(encode_dict['input_ids'])
        input_masks.append(encode_dict['attention_mask'])
        input_types.append(encode_dict['token_type_ids'])

        labels.append(int(y))

input_ids, input_masks, input_types = np.array(input_ids), np.array(input_masks), np.array(input_types)
labels = np.array(labels)
# 3 切分训练集train、验证集valid和测试集test
# 随机打乱索引
idx = np.arange(input_ids.shape[0])
np.random.seed(2019)
np.random.shuffle(idx)
print(idx)

# 8:1:1 划分训练集、验证集、测试集
input_ids_train, input_ids_valid, input_ids_test = input_ids[idx[:80]], input_ids[idx[80:90]], input_ids[idx[90:]]
input_masks_train, input_masks_valid, input_masks_test = input_masks[idx[:80]], input_masks[idx[80:90]], input_masks[idx[90:]]
input_types_train, input_types_valid, input_types_test = input_types[idx[:80]], input_masks[idx[80:90]], input_masks[idx[90:]]
labels_train, labels_valid, labels_test = labels[idx[:80]], labels[idx[80:90]], labels[idx[90:]]

train_dataset = TensorDataset(torch.LongTensor(input_ids_train),
                              torch.LongTensor(input_masks_train),
                              torch.LongTensor(input_types_train),
                              torch.LongTensor(labels_train))
valid_dataset = TensorDataset(torch.LongTensor(input_ids_valid),
                              torch.LongTensor(input_masks_valid),
                              torch.LongTensor(input_types_valid),
                              torch.LongTensor(labels_valid))
test_dataset = TensorDataset(torch.LongTensor(input_ids_test),
                             torch.LongTensor(input_masks_test),
                             torch.LongTensor(input_types_test),
                             torch.LongTensor(labels_test))
# 4 加载到pytorch的DataLoader
batch_size = 16
train_sampler = RandomSampler(train_dataset)
valid_sampler = SequentialSampler(valid_dataset)
test_sampler = SequentialSampler(test_dataset)

train_loader = DataLoader(train_dataset, sampler=train_sampler, shuffle=False, batch_size=batch_size, drop_last=False)
valid_loader = DataLoader(valid_dataset, sampler=valid_sampler, shuffle=False, batch_size=batch_size, drop_last=False)
test_loader = DataLoader(test_dataset, sampler=test_sampler, shuffle=False, batch_size=batch_size, drop_last=False)


# 5 定义bert模型
# 定义model
class Bert_Model(nn.Module):
    def __init__(self, classes=10):
        super(Bert_Model, self).__init__()
        self.config = BertConfig.from_pretrained(bert_path)
        self.bert = BertModel.from_pretrained(bert_path)

        self.fc = nn.Linear(self.config.hidden_size, classes)

    def forward(self, input_ids, attention_masks=None, token_type_ids=None):
        output = self.bert(input_ids, attention_masks, token_type_ids)
        output_pool = output[1]
        logit = self.fc(output_pool)  # [bs, classes]
        return logit

# 6 实例化bert模型
# 打印模型超参数个数
def get_model_parameters(model):
    total_num = sum(p.numel() for p in model.parameters())
    trainable_num = sum(p.numel() for p in model.parameters() if p.requires_grad)
    return 'total_num: %s, trainable_num: %s' % (total_num, trainable_num)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
model = Bert_Model().to(device)
print(get_model_parameters(model))
epoch = 5

# 7 定义优化器
optimizer = AdamW(params=model.parameters(), lr=2e-5, weight_decay=1e-4)
scheduler = get_cosine_schedule_with_warmup(optimizer,
                                            num_warmup_steps=len(train_loader),
                                            num_training_steps=epoch*len(train_loader))
print(len(train_loader))  # 结果为5，即80/16=5

# 8 定义训练函数和验证测试函数
def evaluate(model, valid_loader):
    model.eval()
    valid_true = []
    valid_pred = []
    with torch.no_grad():
        for idx, (ids, masks, types, y) in enumerate(valid_loader):
            y_pred = model(ids.to(device), masks.to(device), types.to(device))
            y_pred = torch.argmax(y_pred, dim=1).detach().cpu().numpy().tolist()

            valid_pred.extend(y_pred)
            valid_true.extend(y.squeeze().detach().cpu().numpy().tolist())
    return accuracy_score(valid_true, valid_pred)


def predict(model, test_loader):
    model.eval()
    test_pred = []
    with torch.no_grad():
        for idx, (ids, masks, types, y) in enumerate(test_loader):
            y_pred = model(ids.to(device), masks.to(device), types.to(device))
            y_pred = torch.argmax(y_pred, dim=1).detach().cpu().numpy().tolist()

            test_pred.extend(y_pred)
    return test_pred


def train_and_eval(model, train_loader, valid_loader, optimizer, scheduler, epoch, device):
    best_acc = 0.00
    criterion = nn.CrossEntropyLoss()
# 训练模型
    for i in range(epoch):
        start_time = time.time()
        model.train()
        total_loss = 0
        print('***** running training epoch {} *****'.format(i + 1))
        for idx, (ids, masks, types, y) in enumerate(train_loader):
            y_pred = model(ids.to(device), masks.to(device), types.to(device))
            y = y.to(device)
            loss = criterion(y_pred, y)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            scheduler.step()
            total_loss += loss.item()
            if (idx + 1) % (len(train_loader) // 5) == 0:
                print('epoch {:04d} | step {:04d}/{:04d} | loss {:.4f} | time {:.4f}'
                      .format(i + 1, idx + 1, len(train_loader), total_loss / (idx + 1), time.time() - start_time))

# 验证模型，模型经训练后传入下面
        model.eval()
        acc = evaluate(model, valid_loader)
        if acc > best_acc:
            best_acc = acc
            torch.save(model.state_dict(), 'best_bert_model.pth')

        print('current accuracy is {:.4f}, best accuracy is {:.4f}' .format(acc, best_acc))
        print('time costed is {:.4f}' .format(time.time() - start_time))


# 9 开始训练和验证模型
train_and_eval(model, train_loader, valid_loader, optimizer, scheduler, epoch, device)

# 10 加载最优模型进行测试
# 加载最优权重对测试集测试（对训练集进行训练后，根据验证集得出最高accuracy的参数，然后对测试集进行测试，预测测试集对应的y）
model.load_state_dict(torch.load('best_bert_model.pth'))
pred_test = predict(model, test_loader)
print('\n Test Accuracy = {} \n'.format(accuracy_score(labels_test, pred_test)))
print(classification_report(labels_test, pred_test, digits=4))
