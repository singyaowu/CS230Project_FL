from collections import OrderedDict
from typing import Dict, Tuple, List
from flwr.common import NDArrays, Scalar

import hydra
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf
import numpy as np
import torch
import flwr as fl

from model import Predictor, train, test
from dataset import prepare_dataset

class FlowerClient(fl.client.NumPyClient):
    """Define a Flower Client."""

    def __init__(self, cid, trainloader, valloader) -> None:
        super().__init__()

        # the dataloaders that point to the data associated to this client
        self.cid = cid
        self.trainloader = trainloader
        self.valloader = valloader

        # a model that is randomly initialised at first
        self.model = Predictor()

        # figure out if this client has access to GPU support or not
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    def set_parameters(self, parameters):
        """Receive parameters and apply them to the local model."""
        params_dict = zip(self.model.state_dict().keys(), parameters)

        state_dict = OrderedDict({k: torch.Tensor(v) for k, v in params_dict})

        self.model.load_state_dict(state_dict, strict=True)

    def get_parameters(self, config: Dict[str, Scalar]):
        """Extract model parameters and return them as a list of numpy arrays."""

        return [val.cpu().numpy() for _, val in self.model.state_dict().items()]

    def fit(self, parameters: List[np.ndarray], config: Dict[str, str]
    ) -> Tuple[List[np.ndarray], int, Dict]:
        """Train model received by the server (parameters) using the data.

        that belongs to this client. Then, send it back to the server.
        """

        # copy parameters sent by the server into client's local model
        self.set_parameters(parameters)

        # fetch elements in the config sent by the server. Note that having a config
        # sent by the server each time a client needs to participate is a simple but
        # powerful mechanism to adjust these hyperparameters during the FL process. For
        # example, maybe you want clients to reduce their LR after a number of FL rounds.
        # or you want clients to do more local epochs at later stages in the simulation
        # you can control these by customising what you pass to `on_fit_config_fn` when
        # defining your strategy.
        lr = config["lr"]
        momentum = config["momentum"]
        epochs = config["local_epochs"]

        # a very standard looking optimiser
        optim = torch.optim.SGD(self.model.parameters(), lr=lr, momentum=momentum)

        # do local training. This function is identical to what you might
        # have used before in non-FL projects. For more advance FL implementation
        # you might want to tweak it but overall, from a client perspective the "local
        # training" can be seen as a form of "centralised training" given a pre-trained
        # model (i.e. the model received from the server)
        train(self.model, self.trainloader, optim, epochs, self.device)

        # Flower clients need to return three arguments: the updated model, the number
        # of examples in the client (although this depends a bit on your choice of aggregation
        # strategy), and a dictionary of metrics (here you can add any additional data, but these
        # are ideally small data structures)
        return self.get_parameters({}), len(self.trainloader), {}

    def evaluate(self, parameters: NDArrays, config: Dict[str, Scalar])-> Tuple[float, int, Dict]:
        self.set_parameters(parameters)

        loss, mean_loss = test(self.model, self.valloader, self.device)
        return float(loss), len(self.valloader), {"cid": self.cid, "mean_loss": mean_loss}


def generate_client_fn(trainloaders, valloaders):
    """Return a function that can be used by the VirtualClientEngine.

    to spawn a FlowerClient with client id `cid`.
    """

    def client_fn(cid: str):
        # This function will be called internally by the VirtualClientEngine
        # Each time the cid-th client is told to participate in the FL
        # simulation (whether it is for doing fit() or evaluate())

        # Returns a normal FLowerClient that will use the cid-th train/val
        # dataloaders as it's local data.
        return FlowerClient(
            cid=cid,
            trainloader=trainloaders[int(cid)],
            valloader=valloaders[int(cid)]
        )

    # return the function to spawn client
    return client_fn

@hydra.main(config_path="conf", config_name="base", version_base=None)
def main(cfg: DictConfig):
    """Load data, start centralizedClient."""

    # Load model and data
    trainloaders, valloaders, _ = prepare_dataset(
        cfg.datasets, cfg.num_clients, cfg.batch_size, is_uniform=cfg.uniform_data_distribution
    )

    # Start client
    fl.client.start_numpy_client(
        server_address=cfg.server_address, 
        client=FlowerClient(cfg.client_id, trainloaders[cfg.client_id], valloaders[cfg.client_id]))


if __name__ == "__main__":
    main()