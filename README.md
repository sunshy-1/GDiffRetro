## GDiffRetro: Retrosynthesis Prediction with Dual Graph Enhanced Molecular Representation and Diffusion Generation
This is the Pytorch implementation for our *AAAI'25* paper: [**GDiffRetro: Retrosynthesis Prediction with Dual Graph Enhanced Molecular Representation and Diffusion Generation**](https://arxiv.org/abs/2501.08001). 

## Abstract
<div style="text-align: justify;">
Retrosynthesis prediction focuses on identifying reactants capable of synthesizing a target product. Typically, the retrosynthesis prediction involves two phases: Reaction Center Identification and Reactant Generation. However, we argue that most existing methods suffer from two limitations in the two phases: (i) Existing models do not adequately capture the ``face'' information in molecular graphs for the reaction center identification. (ii) Current approaches for the reactant generation predominantly use sequence generation in a 2D space, which lacks versatility in generating reasonable distributions for completed reactive groups and overlooks molecules' inherent 3D properties. To overcome the above limitations, we propose GDiffRetro. For the reaction center identification, GDiffRetro uniquely integrates the original graph with its corresponding dual graph to represent molecular structures, which helps guide the model to focus more on the faces in the graph. For the reactant generation, GDiffRetro employs a conditional diffusion model in 3D to further transform the obtained synthon into a complete reactant. Our experimental findings reveal that GDiffRetro outperforms state-of-the-art semi-template models across various evaluative metrics. The overall framework is as follows:
<div> 
<br>

![Framework](fig/framework.png)

# Code Coming Soon ...


## Acknowledgment of Open-Source Code Contributions  

  The code is based on the open-source repositories: [TorchDrug](https://github.com/DeepGraphLearning/torchdrug), [DeLinker](https://github.com/oxpig/DeLinker), and [DiffLinker](https://github.com/igashov/DiffLinker), many thanks to the authors! 

You are welcome to cite our paper:
```
@inproceedings{hcmkr2024,
  author = {Sun, Shengyin and Chen, Ma},
  title = {Hyperbolic Contrastive Learning with Model-Augmentation for Knowledge-Aware Recommendation},
  year = {2024},
  booktitle = {Machine Learning and Knowledge Discovery in Databases},
  pages = {199–217}
}
```
