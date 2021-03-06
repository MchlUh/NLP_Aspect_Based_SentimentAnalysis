# install packages
packages = ['torch', 'transformers', 'pytorch-pretrained-bert', 'pytorch-nlp']
import subprocess

def install(package):
    subprocess.call(["pip", "install", package])

for package in packages:
    install(package)

# import libraries
import torch
from torch.utils.data import TensorDataset, DataLoader, RandomSampler, SequentialSampler
#from keras.preprocessing.sequence import pad_sequences
#from sklearn.model_selection import train_test_split
from transformers import BertTokenizer, BertConfig, BertModel, BertForSequenceClassification, AdamW
from tqdm import tqdm, trange
import pandas as pd
import io
import numpy as np
from sklearn.preprocessing import LabelEncoder
#import matplotlib.pyplot as plt
#import tensorflow as tf
import os

class Classifier:
    """The Classifier"""

    def __init__(self, max_length=128, batch_size=32, epochs=4):
        self.max_length = max_length
        self.batch_size = batch_size
        self.epochs = epochs

    ### LOAD FILES ###

    def loadfile(self, file):
        df = pd.read_csv(file, sep='\t', header=None, names=['polarity', 'aspect', 'target', 'position', 'sentence'])
        return df

    def adjusted_tokenizer(self, df):

        # Define tokenizer
        tokenizer = BertTokenizer.from_pretrained('bert-large-uncased', do_lower_case=True)
        tokenizer.add_tokens(list(df.aspect.str.lower().unique()))

        return tokenizer

    ### PREPROCESSING ###

    def preprocessing(self, df, tokenizer, train=True):

        # Create sentences

        def create_first_input_ids(df):
            first_sentences = df.sentence.values
            first_tokens = [tokenizer.tokenize(sentence) for sentence in first_sentences]
            first_input_ids = [tokenizer.convert_tokens_to_ids(tokens) for tokens in first_tokens]
            #print('first_sentences: ', first_sentences[3], '\nfirst_input_ids: ', first_input_ids[3])
            return first_input_ids

        first_input_ids = create_first_input_ids(df)

        # Auxiliary sentences
        def create_auxiliary_input_ids(df):
            auxiliary_sentences = ["" + aspect + " - " + target
                                   for aspect, target
                                   in list(zip(df.aspect.str.lower().values, df.target.str.lower().values))]
            # auxiliary_sentences = ['neutral' for aspect,target
            #                        in list(zip(df.aspect.values, df.target.values))]
            auxiliary_tokens = [tokenizer.tokenize(sentence) for sentence in auxiliary_sentences]
            auxiliary_input_ids = [tokenizer.convert_tokens_to_ids(tokens) for tokens in auxiliary_tokens]
            #print('auxiliary_sentence: ', auxiliary_sentences[0], '\nauxiliary_tokens: ', auxiliary_tokens[0],
                  #'\nauxiliary_input_ids: ', auxiliary_input_ids[0])
            return auxiliary_input_ids

        auxiliary_input_ids = create_auxiliary_input_ids(df)

        # Encode labels
        encoder = LabelEncoder()
        labels = encoder.fit_transform(df.polarity.values)

        def prepare_input_ids(first_input_ids, auxiliary_input_ids):
            input_ids_prepared = [tokenizer.
                prepare_for_model(
                input_ids_0,
                input_ids_1,
                max_length=self.max_length,
                truncation_strategy='only_first',
                pad_to_max_length=True,
                return_token_type_ids=True,
                return_attention_mask=True) for
                input_ids_0, input_ids_1 in
                list(zip(first_input_ids, auxiliary_input_ids))]

            df_input_ids_prepared = pd.DataFrame(input_ids_prepared)

            input_ids = list(df_input_ids_prepared.input_ids.values)
            token_type_ids = list(df_input_ids_prepared.token_type_ids.values)
            attention_masks = list(df_input_ids_prepared.attention_mask.values)

            return input_ids, token_type_ids, attention_masks

        input_ids, token_type_ids, attention_masks = prepare_input_ids(
            first_input_ids, auxiliary_input_ids)


        # Convert all of our data into torch tensors, the required datatype for our model
        def convert_to_tensors(input_ids, token_type_ids, attention_masks, labels):
            input_ids = torch.tensor(input_ids)
            labels = torch.tensor(labels)
            attention_masks = torch.tensor(attention_masks)
            token_type_ids = torch.tensor(token_type_ids)
            return input_ids, token_type_ids, attention_masks, labels

        input_ids, token_type_ids, attention_masks, labels = convert_to_tensors(
            input_ids, token_type_ids, attention_masks, labels)


        # Create an iterator of our data with torch DataLoader

        def create_data_loader(input_ids, token_type_ids, attention_masks, labels, sampler, batch_size = self.batch_size):
            data = TensorDataset(input_ids, token_type_ids, attention_masks, labels)
            sampler = sampler(data)
            dataloader = DataLoader(data, sampler=sampler, batch_size=batch_size)
            return dataloader


        if train:
            sampler = RandomSampler
            dataloader = create_data_loader(
                input_ids,
                token_type_ids,
                attention_masks,
                labels, sampler=sampler, batch_size=self.batch_size)
        else:
            sampler = SequentialSampler
            dataloader = create_data_loader(
                input_ids,
                token_type_ids,
                attention_masks,
                labels, sampler=sampler, batch_size=len(input_ids))

        return dataloader, encoder

    ## MODEL ##

    def bert_model(self, train_dataloader, path, tokenizer):

        # GPUs
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        n_gpu = torch.cuda.device_count()
        torch.cuda.get_device_name(0)

        # Load BertForSequenceClassification, the pretrained BERT model with a single linear classification layer on top. 
        model = BertForSequenceClassification.from_pretrained("bert-large-uncased", num_labels=3)
        model.resize_token_embeddings(len(tokenizer))
        model.cuda()

        # Parameters
        param_optimizer = list(model.named_parameters())

        # This variable contains all of the hyperparemeter information our training loop needs
        optimizer = AdamW(params=model.parameters(), lr=2e-5, weight_decay=0.00)

        # MODEL TRAINING --

        t = []

        # Store our loss and accuracy for plotting
        train_loss_set = []

        # Number of training epochs (authors recommend between 2 and 4)
        epochs = self.epochs

        # trange is a tqdm wrapper around the normal python range
        for _ in trange(epochs, desc="Epoch"):

            # Training

            # Set our model to training mode (as opposed to evaluation mode)
            model.train()

            # Tracking variables
            tr_loss = 0
            nb_tr_examples, nb_tr_steps = 0, 0

            # Train the data for one epoch
            for step, batch in enumerate(train_dataloader):
                # Add batch to GPU
                batch = tuple(t.to(device) for t in batch)
                # Unpack the inputs from our dataloader
                b_input_ids, b_token_type_ids, b_attention_masks, b_labels = batch
                # Clear out the gradients (by default they accumulate)
                optimizer.zero_grad()
                # Forward pass
                loss, logits = model(b_input_ids, token_type_ids=b_token_type_ids, attention_mask=b_attention_masks,
                                     labels=b_labels)
                train_loss_set.append(loss.item())
                # Backward pass
                loss.backward()
                # Update parameters and take a step using the computed gradient
                optimizer.step()

                # Update tracking variables
                tr_loss += loss.item()
                nb_tr_examples += b_input_ids.size(0)
                nb_tr_steps += 1

        print("Train loss: {}".format(tr_loss / nb_tr_steps))
        torch.save(model.state_dict(), path + '/model.pt')

        ## LOAD MODEL ##

    def load_model(self, path, tokenizer):
        model = BertForSequenceClassification.from_pretrained("bert-large-uncased", num_labels=3)
        model.resize_token_embeddings(len(tokenizer))
        model.load_state_dict(torch.load('{}/model.pt'.format(path)))
        # model.cuda()
        return model

    ## VALIDATION ##

    def bert_eval(self, model, validation_dataloader, encoder):

        # GPUs
        # device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        # n_gpu = torch.cuda.device_count()
        # torch.cuda.get_device_name(0)

        model.eval()

        for batch in validation_dataloader:
            # Add batch to GPU
            # batch = tuple(t.to(device) for t in batch)
            # Unpack the inputs from our dataloader
            b_input_ids, b_token_type_ids, b_attention_masks, b_labels = batch
            # Telling the model not to compute or store gradients, saving memory and speeding up validation
            with torch.no_grad():
                # Forward pass, calculate logit predictions
                (loss, logits) = model(b_input_ids, token_type_ids=b_token_type_ids, attention_mask=b_attention_masks,
                                       labels=b_labels)

                logits = logits.detach().numpy()
                logits = np.argmax(logits, axis=1).flatten()

        preds = encoder.inverse_transform(logits)
        return preds

        #############################################

    def train(self, trainfile):
        """Trains the classifier model on the training set stored in file trainfile"""

        df = self.loadfile(trainfile)
        tokenizer = self.adjusted_tokenizer(df)
        train_dataloader, encoder = self.preprocessing(df, tokenizer, train=True)
        path = os.getcwd()
        model = self.bert_model(train_dataloader, path, tokenizer)

    def predict(self, datafile):
        """Predicts class labels for the input instances in file 'datafile'
        Returns the list of predicted labels
        """

        df = self.loadfile(datafile)
        tokenizer = self.adjusted_tokenizer(df)

        # df = self.loadfile(datafile)
        validation_dataloader, encoder = self.preprocessing(df, tokenizer, train=False)
        path = os.getcwd()
        model = self.load_model(path, tokenizer)
        predicted_labels = self.bert_eval(model, validation_dataloader, encoder)
        print(predicted_labels[0])

        return predicted_labels
