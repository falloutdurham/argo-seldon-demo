
import torch
import torch.nn.functional as F
import os
from transformers import GPT2LMHeadModel, GPT2Tokenizer
import uuid

model_location = f"/mnt/parameters/{uuid.uuid4()}"
os.mkdir(model_location)

MODEL_CLASSES = {
			'gpt2': (GPT2LMHeadModel, GPT2Tokenizer)
		}
model_class, tokenizer_class = MODEL_CLASSES['gpt2']

model = model_class.from_pretrained('gpt2')

model.save_pretrained(f"{model_location}")

with open("/tmp/model-location","w") as loc:
	print(f"{model_location}", file=loc)
